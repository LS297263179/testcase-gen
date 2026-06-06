"""Flask Web 应用"""

import functools
import json
import logging
import os
import tempfile
import time
import traceback
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# 统一日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("web")

from flask import (Flask, Response, jsonify, render_template,
                   request, send_file, session, stream_with_context)

import db
from llm_client import LLMClient, build_client, load_config
from output import to_excel, to_markdown
from preferences import compute_diffs, extract_preferences
from reader import (get_image_media_type, image_to_base64, is_image,
                    read_excel, read_text)
from reviewer import optimize_testcases, review_testcases

app = Flask(__name__)
# secret_key: 优先从环境变量读取，其次从 config.yaml，最后随机生成（重启后失效）
_secret_from_env = os.environ.get("FLASK_SECRET_KEY")
_secret_from_cfg = None
try:
    import yaml
    _cfg_path = Path(__file__).parent / "config.yaml"
    if _cfg_path.exists():
        with open(_cfg_path, encoding="utf-8") as _f:
            _secret_from_cfg = yaml.safe_load(_f).get("secret_key")
except Exception:
    pass
app.secret_key = _secret_from_env or _secret_from_cfg or os.urandom(24)


# ============================================================
# 认证
# ============================================================

def login_required(f):
    """登录校验装饰器"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "未登录，请先登录"}), 401
        return f(*args, **kwargs)
    return decorated


# 初始化数据库
db.init_db()
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32MB（支持多张图片）

# 启动时清理过期输出文件
_cleanup_old_output_files()

OUTPUT_DIR = "./output"
OUTPUT_FILE_MAX_AGE_DAYS = 7  # 输出文件保留天数


def _cleanup_old_output_files():
    """清理超过保留天数的输出文件（每次启动时 + 每次生成后调用）"""
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


def _process_uploaded_files(files) -> tuple[list[dict], str]:
    """处理上传的文件，返回 (images, text_content)。
    图片转为 base64，Excel/TXT 读取为文本。
    """
    from reader import read_excel, read_text
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


# ============================================================
# 全局异常处理
# ============================================================

@app.before_request
def _log_request():
    """记录请求日志"""
    if request.path.startswith("/api/") and request.path != "/api/health":
        logger.info(f"{request.method} {request.path} user={session.get('username', '-')} ip={_get_real_ip()}")


@app.errorhandler(404)
def not_found(e):
    """404 统一返回 JSON"""
    if request.path.startswith("/api/"):
        return jsonify({"error": "接口不存在"}), 404
    return e.get_response() if hasattr(e, 'get_response') else ("Not Found", 404)


@app.errorhandler(500)
def internal_error(e):
    """500 统一返回 JSON"""
    if request.path.startswith("/api/"):
        return jsonify({"error": "服务器内部错误"}), 500
    return e.get_response() if hasattr(e, 'get_response') else ("Internal Server Error", 500)


@app.errorhandler(413)
def too_large(e):
    """文件过大"""
    return jsonify({"error": "上传文件过大，最大支持 32MB"}), 413


# ============================================================
# 速率限制（基于 IP，保护登录/注册接口）
# ============================================================

_rate_limit_store: dict[str, list[float]] = {}  # {ip: [timestamp, ...]}
_rate_limit_lock = __import__("threading").Lock()  # 速率限制字典锁
RATE_LIMIT_MAX = 10    # 每个窗口最多 10 次
RATE_LIMIT_WINDOW = 60 # 窗口大小：60 秒
_rate_limit_last_cleanup = 0.0


def _get_real_ip() -> str:
    """获取真实客户端 IP（兼容反向代理）"""
    # 优先从 X-Forwarded-For 获取（反向代理场景）
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        # 取第一个 IP（最上游的客户端 IP）
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _check_rate_limit(ip: str) -> bool:
    """检查 IP 是否超过速率限制，返回 True 表示允许，False 表示拒绝"""
    global _rate_limit_last_cleanup
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    with _rate_limit_lock:
        # 定期清理所有过期条目（每 60 秒一次），防止内存泄漏
        if now - _rate_limit_last_cleanup > RATE_LIMIT_WINDOW:
            _rate_limit_last_cleanup = now
            expired_ips = [
                k for k, v in _rate_limit_store.items()
                if not v or v[-1] <= window_start
            ]
            for k in expired_ips:
                del _rate_limit_store[k]

        # 清理当前 IP 的过期时间戳
        timestamps = _rate_limit_store.get(ip, [])
        timestamps = [t for t in timestamps if t > window_start]
        if len(timestamps) >= RATE_LIMIT_MAX:
            return False
        timestamps.append(now)
        _rate_limit_store[ip] = timestamps
        return True


# ============================================================
# 认证 API
# ============================================================

@app.route("/api/register", methods=["POST"])
def api_register():
    """用户注册"""
    if not _check_rate_limit(_get_real_ip()):
        return jsonify({"error": "请求过于频繁，请稍后再试"}), 429
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "请求格式错误，请发送 JSON"}), 400
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400
    if len(username) < 2 or len(username) > 32:
        return jsonify({"error": "用户名长度需在 2-32 个字符之间"}), 400
    if not username.isascii() or not all(c.isalnum() or c in "-_" for c in username):
        return jsonify({"error": "用户名只能包含字母、数字、- 和 _"}), 400
    if len(password) < 8:
        return jsonify({"error": "密码长度至少 8 个字符"}), 400
    if len(password) > 128:
        return jsonify({"error": "密码长度不能超过 128 个字符"}), 400

    try:
        user_id = db.create_user(username, password)
        session["user_id"] = user_id
        session["username"] = username
        return jsonify({"success": True, "user": {"id": user_id, "username": username}})
    except ValueError as e:
        return jsonify({"error": str(e)}), 409


@app.route("/api/login", methods=["POST"])
def api_login():
    """用户登录"""
    if not _check_rate_limit(_get_real_ip()):
        return jsonify({"error": "请求过于频繁，请稍后再试"}), 429
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "请求格式错误，请发送 JSON"}), 400
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400

    user = db.verify_user(username, password)
    if not user:
        return jsonify({"error": "用户名或密码错误"}), 401

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return jsonify({"success": True, "user": user})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    """用户登出"""
    session.clear()
    return jsonify({"success": True})


@app.route("/api/me")
def api_me():
    """获取当前登录用户信息"""
    if "user_id" in session:
        return jsonify({"logged_in": True, "user": {
            "id": session["user_id"],
            "username": session["username"],
        }})
    return jsonify({"logged_in": False})


# ============================================================
# 模型配置 API
# ============================================================

# 预设模型
# provider 字段用于判断 Key 是否可复用（同 provider 共享 Key）
MODEL_PRESETS = {
    "mimo": {
        "name": "MiMo",
        "provider": "mimo",
        "generate": {"api_type": "openai", "base_url": "https://token-plan-cn.xiaomimimo.com/v1", "model": "mimo-v2.5-pro", "image_model": "mimo-v2.5", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enable_thinking": True},
        "review":    {"api_type": "openai", "base_url": "https://token-plan-cn.xiaomimimo.com/v1", "model": "mimo-v2.5-pro", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enabled": True},
    },
    "dashscope-deepseek": {
        "name": "DeepSeek (阿里云)",
        "provider": "dashscope",
        "generate": {"api_type": "openai", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "deepseek-v4-pro", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enable_thinking": False},
        "review":    {"api_type": "openai", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "deepseek-v4-pro", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enabled": True},
    },
    "qwen": {
        "name": "通义千问 (阿里云)",
        "provider": "dashscope",
        "generate": {"api_type": "openai", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-max", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enable_thinking": False},
        "review":    {"api_type": "openai", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-max", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enabled": True},
    },
    "kimi": {
        "name": "Kimi (月之暗面)",
        "provider": "moonshot",
        "generate": {"api_type": "openai", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enable_thinking": False},
        "review":    {"api_type": "openai", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enabled": True},
    },
    "openai": {
        "name": "OpenAI GPT",
        "provider": "openai",
        "generate": {"api_type": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enable_thinking": False},
        "review":    {"api_type": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enabled": True},
    },
}


@app.route("/api/model-presets")
@login_required
def api_model_presets():
    """返回预设模型列表"""
    presets = {k: {"name": v["name"]} for k, v in MODEL_PRESETS.items()}
    return jsonify({"presets": presets})


@app.route("/api/model-config", methods=["GET"])
@login_required
def api_model_config_get():
    """获取当前模型配置"""
    config = db.get_model_config()
    # 去掉 api_key 的中间部分，只显示头尾
    for section in ("generate", "review"):
        if section in config and "api_key" in config[section]:
            key = config[section]["api_key"]
            if key and len(key) > 8:
                config[section]["api_key_hint"] = key[:4] + "****" + key[-4:]
            else:
                config[section]["api_key_hint"] = "****"
    return jsonify({"config": config, "presets": {k: v["name"] for k, v in MODEL_PRESETS.items()}})


@app.route("/api/model-config", methods=["POST"])
@login_required
def api_model_config_set():
    """保存模型配置"""
    data = request.get_json()
    preset = data.get("preset")
    need_key = False

    if preset and preset in MODEL_PRESETS:
        preset_data = MODEL_PRESETS[preset]
        config = {
            "generate": {**preset_data["generate"]},
            "review": {**preset_data["review"]},
        }
        # 智能 API Key 查找：
        # 1. 从已保存的 provider_keys 中查找（用户之前配置过的 key）
        # 2. 从当前配置的同 provider key 中复用
        # 3. 都没有则提示用户输入
        new_provider = preset_data.get("provider", "")
        stored_keys = db.get_setting("provider_keys")
        stored_keys = json.loads(stored_keys) if stored_keys else {}

        # 优先从 provider_keys 查找
        found_key = stored_keys.get(new_provider, "")

        # 如果 provider_keys 没有，从当前配置的同 provider 复用
        if not found_key:
            old = db.get_model_config()
            if old.get("_provider") == new_provider:
                found_key = old.get("generate", {}).get("api_key", "")

        need_key = not found_key

        if found_key:
            for section in ("generate", "review"):
                config[section]["api_key"] = found_key

        config["_provider"] = new_provider
    else:
        # 自定义配置
        config = data.get("config", {})
        # 如果 api_key 为空或含 ****，保留旧值
        old = db.get_model_config()
        for section in ("generate", "review"):
            if section in config:
                key = config[section].get("api_key", "")
                if not key or "****" in key:
                    old_key = old.get(section, {}).get("api_key", "")
                    config[section]["api_key"] = old_key

    db.save_model_config(config)

    # 用户手动填写 key 时，同步保存到 provider_keys 以便后续复用
    gen_key = config.get("generate", {}).get("api_key", "")
    prov = config.get("_provider", "")
    if gen_key and "****" not in gen_key and prov:
        stored = json.loads(db.get_setting("provider_keys") or "{}")
        stored[prov] = gen_key
        db.set_setting("provider_keys", json.dumps(stored))

    return jsonify({"success": True, "need_key": need_key if preset and preset in MODEL_PRESETS else False})


@app.route("/api/dashboard")
@login_required
def api_dashboard():
    """仪表盘统计（一次性返回所有数据，避免多次请求）"""
    user_id = session["user_id"]
    stats = db.get_dashboard_stats(user_id)
    # 合并偏好数量
    prefs = db.list_all_preferences(user_id=user_id)
    stats["preference_count"] = len(prefs)
    # 合并当前模型名
    config = db.get_model_config()
    stats["current_model"] = config.get("generate", {}).get("model", "-")
    return jsonify(stats)


@app.route("/api/analyze", methods=["POST"])
@login_required
def api_analyze():
    """需求分析 - 拆解模块和测试点（SSE 流式）"""
    try:
        data = request.get_json()
        requirement = data.get("requirement", "")
        case_types = data.get("case_types")
        if not requirement.strip():
            return jsonify({"error": "需求内容不能为空"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    def sse_stream():
        try:
            from generator import _analyze_modules
            client = get_generate_client()
            _ct = case_types or load_config()["testcase"]["case_types"]

            yield _sse({"type": "progress", "message": "正在分析需求，拆解功能模块..."})
            complexity, modules = _analyze_modules(client, requirement, _ct, None)

            if not modules:
                yield _sse({"type": "done", "data": {
                    "success": True, "complexity": complexity, "modules": [],
                    "message": "模块分析失败，请直接生成"
                }})
                return

            yield _sse({"type": "done", "data": {
                "success": True,
                "complexity": complexity,
                "modules": modules,
            }})
        except Exception as e:
            logger.exception("SSE 流处理异常")
            yield _sse({"type": "error", "message": "服务器内部错误，请查看日志详情"})

    return Response(
        stream_with_context(sse_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ============================================================
# 测试点生成 API
# ============================================================

TEST_POINTS_PROMPT = """你是一位资深测试专家。请根据以下需求，生成测试点。

需求内容：
{requirement}
{materials}

请返回 JSON 格式的测试点树，结构如下：
[
  {{
    "module": "模块名称",
    "points": [
      {{"title": "测试点标题", "description": "简要描述"}},
      ...
    ]
  }}
]

要求：
1. 按功能模块分组
2. 每个模块下列出关键测试点（正常流程、边界、异常）
3. 测试点要具体可执行
4. 结合项目资料中的信息补充测试点
5. 只返回 JSON，不要其他内容"""


@app.route("/api/generate-points", methods=["POST"])
@login_required
def api_generate_points():
    """生成测试点（SSE 流式）"""
    try:
        requirement = ""
        images = []
        material_ids = None
        is_multipart = request.content_type and "multipart/form-data" in request.content_type

        if is_multipart:
            requirement = request.form.get("requirement", "")
            material_ids_raw = request.form.get("material_ids", "")
            material_ids = [int(x) for x in material_ids_raw.split(",") if x.strip()] if material_ids_raw else None
            images, file_text = _process_uploaded_files(request.files.getlist("files"))
            if file_text:
                requirement += "\n" + file_text
        else:
            data = request.get_json()
            requirement = data.get("requirement", "")

        if not requirement.strip() and not images:
            return jsonify({"error": "需求内容不能为空"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    def sse_stream():
        try:
            client = get_generate_client()
            image_client = get_image_client() if images else None
            active_client = image_client if (images and image_client) else client

            prompt = TEST_POINTS_PROMPT.format(
                requirement=requirement or "（见图片）",
                materials=db.get_materials_for_prompt(session["user_id"], material_ids),
            )

            yield _sse({"type": "progress", "message": "正在分析需求，生成测试点..."})
            response = active_client.chat("你是一位资深测试专家。", prompt, images=images if images else None)
            text = response.strip()

            # 提取 JSON
            import re
            json_match = re.search(r'\[[\s\S]*\]', text)
            if json_match:
                points = json.loads(json_match.group())
            else:
                points = json.loads(text)

            total = sum(len(m.get("points", [])) for m in points)
            # 自动保存到数据库
            title = (requirement or "测试点").strip()[:30]
            tp_id = db.save_test_points(session["user_id"], title, requirement, points, total)

            yield _sse({"type": "done", "data": {
                "success": True,
                "points": points,
                "total": total,
                "tp_id": tp_id,
            }})
        except Exception as e:
            logger.exception("SSE 流处理异常")
            yield _sse({"type": "error", "message": "服务器内部错误，请查看日志详情"})

    return Response(
        stream_with_context(sse_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/export-points", methods=["POST"])
@login_required
def api_export_points():
    """导出测试点为 MD 或 XMIND"""
    data = request.get_json()
    points = data.get("points", [])
    fmt = data.get("format", "md")
    title = data.get("title", "测试点")

    if not points:
        return jsonify({"error": "无测试点数据"}), 400

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if fmt == "md":
        lines = [f"# {title}\n"]
        for module in points:
            lines.append(f"## {module.get('module', '未分类')}\n")
            for p in module.get("points", []):
                lines.append(f"- **{p.get('title', '')}**：{p.get('description', '')}")
            lines.append("")
        content = "\n".join(lines)
        path = os.path.join(OUTPUT_DIR, f"testpoints_{timestamp}.md")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return jsonify({"success": True, "file": os.path.basename(path)})

    elif fmt == "xmind":
        # XMind 导出暂不可用（xmind 库 API 不兼容）
        # 如需启用，请安装兼容的 xmind 库并更新此段代码
        return jsonify({"error": "XMind 导出暂不可用，请使用 Markdown 格式导出"}), 501

    return jsonify({"error": "不支持的格式"}), 400


# ============================================================
# 项目资料 API
# ============================================================

@app.route("/api/materials", methods=["GET"])
@login_required
def api_materials_list():
    """列出当前用户的项目资料"""
    materials = db.list_materials(session["user_id"])
    return jsonify({"materials": materials})


@app.route("/api/materials", methods=["POST"])
@login_required
def api_materials_create():
    """创建项目资料（支持图片上传）"""
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "")
    if not title:
        return jsonify({"error": "标题不能为空"}), 400

    images, _ = _process_uploaded_files(request.files.getlist("images"))
    mid = db.create_material(session["user_id"], title, content, images)
    return jsonify({"success": True, "id": mid})


@app.route("/api/materials/<int:mid>", methods=["GET"])
@login_required
def api_materials_get(mid):
    """获取单条项目资料"""
    m = db.get_material(mid)
    if not m or m["user_id"] != session["user_id"]:
        return jsonify({"error": "资料不存在"}), 404
    return jsonify(m)


@app.route("/api/materials/<int:mid>", methods=["DELETE"])
@login_required
def api_materials_delete(mid):
    """删除项目资料"""
    m = db.get_material(mid)
    if not m or m["user_id"] != session["user_id"]:
        return jsonify({"error": "资料不存在"}), 404
    db.delete_material(mid)
    return jsonify({"success": True})


# ============================================================
# 测试点历史 API
# ============================================================

@app.route("/api/test-points", methods=["GET"])
@login_required
def api_test_points_list():
    """列出当前用户的测试点记录"""
    points = db.list_test_points(session["user_id"])
    return jsonify({"test_points": points})


@app.route("/api/test-points/<int:tp_id>", methods=["GET"])
@login_required
def api_test_points_get(tp_id):
    """获取单条测试点记录"""
    tp = db.get_test_points(tp_id)
    if not tp or tp["user_id"] != session["user_id"]:
        return jsonify({"error": "记录不存在"}), 404
    return jsonify(tp)


@app.route("/api/test-points/<int:tp_id>", methods=["DELETE"])
@login_required
def api_test_points_delete(tp_id):
    """删除测试点记录"""
    tp = db.get_test_points(tp_id)
    if not tp or tp["user_id"] != session["user_id"]:
        return jsonify({"error": "记录不存在"}), 404
    db.delete_test_points(tp_id)
    return jsonify({"success": True})


def get_generate_client() -> LLMClient:
    config = db.get_model_config()
    return build_client(config["generate"])


def get_review_client() -> LLMClient:
    config = db.get_model_config()
    review_cfg = config.get("review", {})
    if review_cfg.get("enabled", False):
        return build_client(review_cfg)
    return build_client(config["generate"])


def get_image_client() -> LLMClient | None:
    config = db.get_model_config()
    gen_cfg = config["generate"]
    image_model = gen_cfg.get("image_model")
    if not image_model:
        return None
    cfg = {**gen_cfg, "model": image_model}
    return build_client(cfg)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def api_health():
    """健康检查端点（检查数据库连通性）"""
    checks = {"status": "ok"}
    try:
        with db.db_read_conn() as conn:
            conn.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:
        checks["status"] = "degraded"
        checks["database"] = f"error: {e}"
    return jsonify(checks), 200 if checks["status"] == "ok" else 503


@app.route("/api/generate", methods=["POST"])
@login_required
def api_generate():
    """生成测试用例（SSE 流式返回进度 + 结果）"""
    # 先解析请求参数
    priority = None
    case_types = None
    images = []

    is_multipart = request.content_type and "multipart/form-data" in request.content_type

    _VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}
    _VALID_CASE_TYPES = {"功能测试", "边界测试", "异常测试", "兼容性测试", "性能测试", "安全测试", "UI测试"}
    _MAX_REQUIREMENT_LEN = 100_000  # 需求文本最大 10 万字符

    requirement = ""
    try:
        material_ids = None
        test_point_id = None
        if is_multipart:
            requirement = request.form.get("requirement", "")
            if len(requirement) > _MAX_REQUIREMENT_LEN:
                return jsonify({"error": f"需求文本过长，最大 {_MAX_REQUIREMENT_LEN} 字符"}), 400
            priority = request.form.get("priority")
            if priority and priority not in _VALID_PRIORITIES:
                return jsonify({"error": f"无效的优先级: {priority}，可选值: P0/P1/P2/P3"}), 400
            ct = request.form.get("case_types")
            if ct:
                case_types = [x.strip() for x in ct.split(",") if x.strip()]
                invalid = [t for t in case_types if t not in _VALID_CASE_TYPES]
                if invalid:
                    return jsonify({"error": f"无效的用例类型: {', '.join(invalid)}"}), 400
            material_ids_raw = request.form.get("material_ids", "")
            if material_ids_raw:
                try:
                    material_ids = [int(x) for x in material_ids_raw.split(",") if x.strip()]
                except ValueError:
                    return jsonify({"error": "material_ids 格式错误，应为逗号分隔的数字"}), 400
            tp_id_raw = request.form.get("test_point_id", "")
            if tp_id_raw and tp_id_raw.strip():
                try:
                    test_point_id = int(tp_id_raw)
                except ValueError:
                    pass

            images, file_text = _process_uploaded_files(request.files.getlist("files"))
            if file_text:
                requirement += "\n" + file_text
        else:
            data = request.get_json()
            requirement = data.get("requirement", "")
            priority = data.get("priority")
            case_types = data.get("case_types")

        if not requirement.strip() and not images:
            return jsonify({"error": "需求内容不能为空（文本或图片至少提供一项）"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    def sse_stream():
        nonlocal requirement
        try:
            def on_progress(msg: str):
                print(f"[进度] {msg}")

            file_config = load_config()
            default_priority = priority or file_config["testcase"]["default_priority"]
            _case_types = case_types or file_config["testcase"]["case_types"]
            max_testcases = file_config["testcase"].get("max_testcases", 100)

            # 加载用户偏好
            pref_context = db.get_preference_context(user_id=session.get("user_id"))

            # 加载项目材料
            mat_context = db.get_materials_for_prompt(session.get("user_id"), material_ids) if material_ids else ""
            if mat_context:
                requirement = requirement + "\n\n【参考项目材料】\n" + mat_context

            # 加载测试点
            tp_context = db.get_test_points_for_prompt(test_point_id) if test_point_id else ""
            if tp_context:
                requirement = requirement + "\n\n【参考测试点】\n" + tp_context

            # Step 1: 分析模块
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from generator import _analyze_modules, _generate_for_module, _deduplicate, _deduplicate_by_steps, _generate_all_in_one, _limit_testcases
            client = get_generate_client()
            image_client = get_image_client() if images else None
            active_client = image_client if (images and image_client) else client

            yield _sse({"type": "progress", "message": "正在分析需求，拆解功能模块..."})
            complexity, modules = _analyze_modules(active_client, requirement, _case_types, images if images else None)

            if not modules:
                yield _sse({"type": "progress", "message": "模块分析失败，使用一次性生成模式..."})
                testcases = _generate_all_in_one(active_client, requirement, default_priority, _case_types, images if images else None, max_testcases, pref_context or None)
            else:
                # Step 2: 按模块并行生成
                total_modules = len(modules)
                max_workers = min(total_modules, 5)
                complexity_label = {"simple": "简单", "medium": "中等", "complex": "复杂"}.get(complexity, "中等")
                yield _sse({"type": "progress", "message": f"需求复杂度：{complexity_label}，正在并行生成 {total_modules} 个模块的测试用例（{max_workers} 路并发）..."})

                all_testcases = []
                _images = images if images else None
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_module = {
                        executor.submit(_generate_for_module, active_client, requirement, mod, default_priority, _images, complexity, pref_context or None): mod
                        for mod in modules
                    }
                    completed = 0
                    for future in as_completed(future_to_module):
                        mod = future_to_module[future]
                        completed += 1
                        try:
                            cases = future.result()
                            all_testcases.extend(cases)
                            yield _sse({"type": "progress", "message": f"「{mod['name']}」模块完成，生成 {len(cases)} 条用例 ({completed}/{total_modules})"})
                        except Exception as e:
                            yield _sse({"type": "progress", "message": f"「{mod['name']}」模块生成失败: {e} ({completed}/{total_modules})"})

                if not all_testcases:
                    raise ValueError("分段生成未产出任何用例")

                # Step 3: 去重编号
                raw_count = len(all_testcases)
                testcases = _deduplicate(all_testcases)
                dedup_count = raw_count - len(testcases)
                if dedup_count > 0:
                    yield _sse({"type": "progress", "message": f"精确去重完成，移除 {dedup_count} 条重复用例"})

                step_dedup_before = len(testcases)
                testcases = _deduplicate_by_steps(testcases)
                step_dedup_count = step_dedup_before - len(testcases)
                if step_dedup_count > 0:
                    yield _sse({"type": "progress", "message": f"步骤语义去重完成，移除 {step_dedup_count} 条相似用例"})

                if len(testcases) > max_testcases:
                    yield _sse({"type": "progress", "message": f"用例数 ({len(testcases)}) 超过上限 {max_testcases}，按优先级保留"})
                    testcases = _limit_testcases(testcases, max_testcases)

                for i, tc in enumerate(testcases):
                    tc["id"] = f"TC_{i + 1:03d}"

            # 导出文件
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            excel_path = to_excel(testcases, OUTPUT_DIR, f"testcases_{timestamp}.xlsx")
            md_path = to_markdown(testcases, OUTPUT_DIR, f"testcases_{timestamp}.md")

            # 清理过期输出文件
            _cleanup_old_output_files()

            # 自动保存到历史记录
            session_id = db.create_session(
                requirement=requirement,
                testcases=testcases,
                priority=default_priority,
                case_types=list(_case_types) if _case_types else None,
                images=images if images else None,
                user_id=session.get("user_id"),
            )

            result = {
                "success": True,
                "count": len(testcases),
                "testcases": testcases,
                "files": {"excel": excel_path, "markdown": md_path},
                "session_id": session_id,
                "input": {
                    "has_text": bool(requirement.strip()),
                    "image_count": len(images),
                    "image_names": [img.get("filename", "") for img in images],
                },
            }
            yield _sse({"type": "done", "data": result})

        except Exception as e:
            logger.exception("SSE 流处理异常")
            yield _sse({"type": "error", "message": "服务器内部错误，请查看日志详情"})

    return Response(
        stream_with_context(sse_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.route("/api/review", methods=["POST"])
@login_required
def api_review():
    """评审测试用例（SSE 流式返回进度）"""
    try:
        data = request.get_json()
        requirement = data.get("requirement", "")
        testcases = data.get("testcases", [])

        if not testcases:
            return jsonify({"error": "没有可评审的用例"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    def sse_stream():
        try:
            client = get_review_client()

            yield _sse({"type": "progress", "message": f"正在分析 {len(testcases)} 条用例..."})
            result = review_testcases(client, requirement, testcases)

            yield _sse({"type": "progress", "message": "正在生成评审报告..."})
            report_path = Path(OUTPUT_DIR) / "review_report.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(f"# 测试用例评审报告\n\n{result}")

            yield _sse({"type": "done", "data": {
                "success": True,
                "review": result,
                "report_path": str(report_path),
            }})
        except Exception as e:
            logger.exception("SSE 流处理异常")
            yield _sse({"type": "error", "message": "服务器内部错误，请查看日志详情"})

    return Response(
        stream_with_context(sse_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/optimize", methods=["POST"])
@login_required
def api_optimize():
    """根据评审报告优化测试用例（SSE 流式返回进度）"""
    try:
        data = request.get_json()
        requirement = data.get("requirement", "")
        testcases = data.get("testcases", [])
        review_report = data.get("review", "")

        if not testcases:
            return jsonify({"error": "没有可优化的用例"}), 400
        if not review_report:
            return jsonify({"error": "请先进行 AI 评审"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    def sse_stream():
        try:
            client = get_generate_client()

            yield _sse({"type": "progress", "message": "正在分析评审报告中的问题和建议..."})
            optimized = optimize_testcases(client, requirement, testcases, review_report)

            yield _sse({"type": "progress", "message": f"正在导出优化后的 {len(optimized)} 条用例..."})
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            excel_path = to_excel(optimized, OUTPUT_DIR, f"testcases_optimized_{timestamp}.xlsx")
            md_path = to_markdown(optimized, OUTPUT_DIR, f"testcases_optimized_{timestamp}.md")

            yield _sse({"type": "done", "data": {
                "success": True,
                "count": len(optimized),
                "testcases": optimized,
                "files": {"excel": excel_path, "markdown": md_path},
            }})
        except Exception as e:
            logger.exception("SSE 流处理异常")
            yield _sse({"type": "error", "message": "服务器内部错误，请查看日志详情"})

    return Response(
        stream_with_context(sse_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/download/<path:filename>")
@login_required
def api_download(filename):
    """下载文件（文件不存在时从历史记录重新生成）"""
    filepath = os.path.realpath(os.path.join(OUTPUT_DIR, filename))
    output_real = os.path.realpath(OUTPUT_DIR)
    if not filepath.startswith(output_real + os.sep) and filepath != output_real:
        return jsonify({"error": "非法路径"}), 403

    # 文件存在直接下载
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)

    # 文件不存在：尝试从历史记录重新生成
    session_id = request.args.get("session_id")
    if session_id:
        try:
            record = db.get_session(int(session_id))
            if record and record.get("user_id") == session.get("user_id"):
                testcases = record.get("testcases", [])
                if testcases:
                    os.makedirs(OUTPUT_DIR, exist_ok=True)
                    if filename.endswith(".xlsx"):
                        to_excel(testcases, OUTPUT_DIR, filename)
                    elif filename.endswith(".md"):
                        to_markdown(testcases, OUTPUT_DIR, filename)
                    if os.path.exists(filepath):
                        return send_file(filepath, as_attachment=True)
        except Exception as e:
            logger.warning(f"重新生成文件失败: {e}")

    return jsonify({"error": "文件不存在，请重新生成"}), 404


# ============================================================
# 历史记录 API
# ============================================================

@app.route("/api/history")
@login_required
def api_history():
    """列出历史记录"""
    limit = min(request.args.get("limit", 50, type=int), 200)  # 上限 200 条
    offset = max(request.args.get("offset", 0, type=int), 0)
    sessions = db.list_sessions(limit=limit, offset=offset, user_id=session["user_id"])
    return jsonify({"sessions": sessions})


@app.route("/api/history/<int:session_id>")
@login_required
def api_history_detail(session_id):
    """获取单条历史记录"""
    record = db.get_session(session_id)
    if not record or record.get("user_id") != session["user_id"]:
        return jsonify({"error": "记录不存在"}), 404
    return jsonify(record)


@app.route("/api/history/<int:session_id>", methods=["DELETE"])
@login_required
def api_history_delete(session_id):
    """删除历史记录"""
    record = db.get_session(session_id)
    if not record or record.get("user_id") != session["user_id"]:
        return jsonify({"error": "记录不存在"}), 404
    db.delete_session(session_id)
    return jsonify({"success": True})


@app.route("/api/history/<int:session_id>/review", methods=["POST"])
@login_required
def api_history_save_review(session_id):
    """为历史记录保存评审报告"""
    record = db.get_session(session_id)
    if not record or record.get("user_id") != session["user_id"]:
        return jsonify({"error": "记录不存在"}), 404
    data = request.get_json()
    review = data.get("review", "")
    if not review:
        return jsonify({"error": "评审报告为空"}), 400
    db.save_review(session_id, review)
    return jsonify({"success": True})


# ============================================================
# 偏好 API
# ============================================================

@app.route("/api/preferences")
@login_required
def api_preferences():
    """列出所有偏好规则"""
    prefs = db.list_all_preferences(user_id=session.get("user_id"))
    return jsonify({"preferences": prefs})


@app.route("/api/preferences/extract", methods=["POST"])
@login_required
def api_preferences_extract():
    """从用户编辑中提取偏好规则（SSE 流式）"""
    try:
        data = request.get_json()
        original = data.get("original", [])
        edited = data.get("edited", [])
        session_id = data.get("session_id")
        if not original or not edited:
            return jsonify({"error": "缺少原始或编辑后的用例"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    def sse_stream():
        try:
            yield _sse({"type": "progress", "message": "正在分析修改差异..."})
            diffs = compute_diffs(original, edited)
            if not diffs:
                yield _sse({"type": "done", "data": {
                    "success": True,
                    "preferences": [],
                    "message": "未检测到有效修改",
                }})
                return

            yield _sse({"type": "progress", "message": f"检测到 {len(diffs)} 处修改，正在提取偏好规则..."})
            client = get_generate_client()
            prefs = extract_preferences(diffs, client)

            if prefs and session_id:
                db.save_preferences(prefs, session_id, source_diffs=diffs,
                                    user_id=session.get("user_id"))

            yield _sse({"type": "done", "data": {
                "success": True,
                "preferences": prefs,
                "count": len(prefs),
            }})
        except Exception as e:
            logger.exception("SSE 流处理异常")
            yield _sse({"type": "error", "message": "服务器内部错误，请查看日志详情"})

    return Response(
        stream_with_context(sse_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/preferences/<int:pref_id>", methods=["PUT"])
@login_required
def api_preferences_update(pref_id):
    """更新偏好规则（启用/禁用/修改）"""
    pref = db.get_preference(pref_id)
    if not pref:
        return jsonify({"error": "规则不存在"}), 404
    # 所有权校验：只允许修改自己的偏好或无主偏好
    if pref.get("user_id") is not None and pref.get("user_id") != session.get("user_id"):
        return jsonify({"error": "无权操作"}), 403
    data = request.get_json()
    active = data.get("active")
    pattern = data.get("pattern")
    db.update_preference(pref_id, active=active, pattern=pattern)
    return jsonify({"success": True})


@app.route("/api/preferences/<int:pref_id>", methods=["DELETE"])
@login_required
def api_preferences_delete(pref_id):
    """删除偏好规则"""
    pref = db.get_preference(pref_id)
    if not pref:
        return jsonify({"error": "规则不存在"}), 404
    if pref.get("user_id") is not None and pref.get("user_id") != session.get("user_id"):
        return jsonify({"error": "无权操作"}), 403
    db.delete_preference(pref_id)
    return jsonify({"success": True})
