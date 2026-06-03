"""Flask Web 应用"""

import functools
import json
import os
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

from flask import (Flask, Response, jsonify, render_template,
                   request, send_file, session, stream_with_context)

import db
from llm_client import LLMClient, load_config
from output import to_excel, to_markdown
from preferences import compute_diffs, extract_preferences
from reader import (get_image_media_type, image_to_base64, is_image,
                    read_excel, read_text)
from reviewer import optimize_testcases, review_testcases

app = Flask(__name__)
app.secret_key = os.urandom(24)


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

OUTPUT_DIR = "./output"


# ============================================================
# 认证 API
# ============================================================

@app.route("/api/register", methods=["POST"])
def api_register():
    """用户注册"""
    data = request.get_json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400
    if len(username) < 2 or len(username) > 32:
        return jsonify({"error": "用户名长度需在 2-32 个字符之间"}), 400
    if len(password) < 4:
        return jsonify({"error": "密码长度至少 4 个字符"}), 400

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
    data = request.get_json()
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
    """仪表盘统计"""
    stats = db.get_dashboard_stats(session["user_id"])
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
            _ct = case_types or load_config("config.yaml")["testcase"]["case_types"]

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
            traceback.print_exc()
            yield _sse({"type": "error", "message": str(e)})

    return Response(
        stream_with_context(sse_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def build_client(cfg: dict) -> LLMClient:
    return LLMClient(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=cfg["model"],
        api_type=cfg.get("api_type", "openai"),
        temperature=cfg.get("temperature", 0.3),
        max_tokens=cfg.get("max_tokens", 4096),
        max_retries=cfg.get("max_retries", 3),
        enable_thinking=cfg.get("enable_thinking", False),
    )


def _load_model_config() -> dict:
    """加载模型配置（数据库优先，fallback config.yaml）"""
    return db.get_model_config()


def get_generate_client() -> LLMClient:
    config = _load_model_config()
    return build_client(config["generate"])


def get_review_client() -> LLMClient:
    config = _load_model_config()
    review_cfg = config.get("review", {})
    if review_cfg.get("enabled", False):
        return build_client(review_cfg)
    return build_client(config["generate"])


def get_image_client() -> LLMClient | None:
    config = _load_model_config()
    gen_cfg = config["generate"]
    image_model = gen_cfg.get("image_model")
    if not image_model:
        return None
    cfg = {**gen_cfg, "model": image_model}
    return build_client(cfg)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/generate", methods=["POST"])
@login_required
def api_generate():
    """生成测试用例（SSE 流式返回进度 + 结果）"""
    # 先解析请求参数
    requirement = ""
    priority = None
    case_types = None
    images = []

    is_multipart = request.content_type and "multipart/form-data" in request.content_type

    try:
        if is_multipart:
            requirement = request.form.get("requirement", "")
            priority = request.form.get("priority")
            ct = request.form.get("case_types")
            if ct:
                case_types = [x.strip() for x in ct.split(",") if x.strip()]

            files = request.files.getlist("files")
            for f in files:
                if not f.filename:
                    continue
                suffix = Path(f.filename).suffix.lower()
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp_path = tmp.name
                f.save(tmp_path)
                try:
                    if is_image(tmp_path):
                        images.append({
                            "data": image_to_base64(tmp_path),
                            "media_type": get_image_media_type(tmp_path),
                            "filename": f.filename,
                        })
                    elif suffix in (".xlsx", ".xls"):
                        requirement += "\n" + read_excel(tmp_path)
                    else:
                        requirement += "\n" + read_text(tmp_path)
                finally:
                    os.unlink(tmp_path)
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
        try:
            def on_progress(msg: str):
                print(f"[进度] {msg}")

            file_config = load_config("config.yaml")
            default_priority = priority or file_config["testcase"]["default_priority"]
            _case_types = case_types or file_config["testcase"]["case_types"]
            max_testcases = file_config["testcase"].get("max_testcases", 100)

            # 加载用户偏好
            pref_context = db.get_preference_context()

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
            traceback.print_exc()
            yield _sse({"type": "error", "message": str(e)})

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
            traceback.print_exc()
            yield _sse({"type": "error", "message": str(e)})

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
            traceback.print_exc()
            yield _sse({"type": "error", "message": str(e)})

    return Response(
        stream_with_context(sse_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/download/<path:filename>")
@login_required
def api_download(filename):
    """下载文件"""
    filepath = os.path.realpath(os.path.join(OUTPUT_DIR, filename))
    output_real = os.path.realpath(OUTPUT_DIR)
    if not filepath.startswith(output_real + os.sep) and filepath != output_real:
        return jsonify({"error": "非法路径"}), 403
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    return jsonify({"error": "文件不存在"}), 404


# ============================================================
# 历史记录 API
# ============================================================

@app.route("/api/history")
@login_required
def api_history():
    """列出历史记录"""
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    sessions = db.list_sessions(limit=limit, offset=offset, user_id=session["user_id"])
    return jsonify({"sessions": sessions})


@app.route("/api/history/<int:session_id>")
@login_required
def api_history_detail(session_id):
    """获取单条历史记录"""
    session = db.get_session(session_id)
    if not session:
        return jsonify({"error": "记录不存在"}), 404
    return jsonify(session)


@app.route("/api/history/<int:session_id>", methods=["DELETE"])
@login_required
def api_history_delete(session_id):
    """删除历史记录"""
    db.delete_session(session_id)
    return jsonify({"success": True})


@app.route("/api/history/<int:session_id>/review", methods=["POST"])
@login_required
def api_history_save_review(session_id):
    """为历史记录保存评审报告"""
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
    prefs = db.list_all_preferences()
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
                db.save_preferences(prefs, session_id, source_diffs=diffs)

            yield _sse({"type": "done", "data": {
                "success": True,
                "preferences": prefs,
                "count": len(prefs),
            }})
        except Exception as e:
            traceback.print_exc()
            yield _sse({"type": "error", "message": str(e)})

    return Response(
        stream_with_context(sse_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/preferences/<int:pref_id>", methods=["PUT"])
@login_required
def api_preferences_update(pref_id):
    """更新偏好规则（启用/禁用/修改）"""
    data = request.get_json()
    active = data.get("active")
    pattern = data.get("pattern")
    db.update_preference(pref_id, active=active, pattern=pattern)
    return jsonify({"success": True})


@app.route("/api/preferences/<int:pref_id>", methods=["DELETE"])
@login_required
def api_preferences_delete(pref_id):
    """删除偏好规则"""
    db.delete_preference(pref_id)
    return jsonify({"success": True})
