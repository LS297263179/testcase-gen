"""Web 层共享工具 - 装饰器、SSE、速率限制、文件处理等"""

import functools
import json
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path

from flask import jsonify, request, session

from core import db
from core.llm_client import LLMClient, build_client
from core.reader import get_image_media_type, image_to_base64, is_image, read_excel, read_text

logger = logging.getLogger("web")

OUTPUT_DIR = "./output"
OUTPUT_FILE_MAX_AGE_DAYS = 7


def get_user_output_dir(user_id: int | None = None) -> str:
    """获取用户专属输出目录，不存在则创建"""
    if user_id:
        path = os.path.join(OUTPUT_DIR, str(user_id))
    else:
        path = OUTPUT_DIR
    os.makedirs(path, exist_ok=True)
    return path


# ============================================================
# 认证 & CSRF 装饰器
# ============================================================

def generate_csrf_token() -> str:
    """生成 CSRF token 并存入 session"""
    token = secrets.token_hex(32)
    session["csrf_token"] = token
    return token


def login_required(f):
    """登录校验装饰器"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "未登录，请先登录"}), 401
        return f(*args, **kwargs)
    return decorated


def csrf_protect(f):
    """CSRF 保护装饰器（POST/PUT/DELETE 请求必须携带有效 token）"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if request.method in ("POST", "PUT", "DELETE"):
            token = request.headers.get("X-CSRF-Token", "")
            expected = session.get("csrf_token", "")
            if not token or not expected or token != expected:
                return jsonify({"error": "CSRF token 无效，请刷新页面"}), 403
        return f(*args, **kwargs)
    return decorated


# ============================================================
# 速率限制（基于 IP）
# ============================================================

import threading

_rate_limit_store: dict[str, list[float]] = {}
_rate_limit_lock = threading.Lock()
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 60
_rate_limit_last_cleanup = 0.0


def get_real_ip() -> str:
    """获取真实客户端 IP（兼容反向代理）"""
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"


def check_rate_limit(ip: str) -> bool:
    """检查 IP 是否超过速率限制，返回 True 表示允许，False 表示拒绝"""
    global _rate_limit_last_cleanup
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    with _rate_limit_lock:
        if now - _rate_limit_last_cleanup > RATE_LIMIT_WINDOW:
            _rate_limit_last_cleanup = now
            expired_ips = [
                k for k, v in _rate_limit_store.items()
                if not v or v[-1] <= window_start
            ]
            for k in expired_ips:
                del _rate_limit_store[k]

        timestamps = _rate_limit_store.get(ip, [])
        timestamps = [t for t in timestamps if t > window_start]
        if len(timestamps) >= RATE_LIMIT_MAX:
            return False
        timestamps.append(now)
        _rate_limit_store[ip] = timestamps
        return True


# ============================================================
# SSE 工具
# ============================================================

def sse_format(data: dict) -> str:
    """格式化 SSE 事件"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ============================================================
# 文件处理
# ============================================================

def process_uploaded_files(files) -> tuple[list[dict], str]:
    """处理上传的文件，返回 (images, text_content)。
    图片转为 base64，Excel/TXT 读取为文本。
    """
    images = []
    text_parts = []
    for f in files:
        if not f.filename:
            continue
        suffix = Path(f.filename).suffix.lower()
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp_path = tmp.name
            f.save(tmp_path)
            if is_image(tmp_path):
                images.append({
                    "data": image_to_base64(tmp_path),
                    "media_type": get_image_media_type(tmp_path),
                    "filename": f.filename,
                })
            elif suffix in (".xlsx", ".xls"):
                text_parts.append(read_excel(tmp_path))
            else:
                text_parts.append(read_text(tmp_path))
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
    return images, "\n".join(text_parts)


def cleanup_old_output_files():
    """清理超过保留天数的输出文件"""
    output_path = Path(OUTPUT_DIR)
    if not output_path.exists():
        return
    now = time.time()
    max_age = OUTPUT_FILE_MAX_AGE_DAYS * 86400
    cleaned = 0
    for f in output_path.iterdir():
        if f.is_file() and (now - f.stat().st_mtime) > max_age:
            try:
                f.unlink()
                cleaned += 1
            except OSError:
                pass
    if cleaned:
        logger.info(f"已清理 {cleaned} 个过期输出文件（>{OUTPUT_FILE_MAX_AGE_DAYS}天）")


# ============================================================
# LLM Client 工厂
# ============================================================

def get_generate_client() -> LLMClient:
    """获取生成用 LLM 客户端"""
    from core.config import get_model_config
    cfg = get_model_config()
    return build_client(cfg["generate"])


def get_review_client() -> LLMClient:
    """获取评审用 LLM 客户端（可能与生成用不同模型）"""
    from core.config import get_model_config
    cfg = get_model_config()
    review_cfg = cfg.get("review", {})
    if review_cfg.get("enabled", False):
        return build_client(review_cfg)
    return build_client(cfg["generate"])


def get_image_client() -> LLMClient | None:
    """获取图片识别 LLM 客户端"""
    from core.config import get_model_config
    cfg = get_model_config()
    gen_cfg = cfg["generate"]
    image_model = gen_cfg.get("image_model")
    if not image_model:
        return None
    client_cfg = {**gen_cfg, "model": image_model}
    return build_client(client_cfg)
