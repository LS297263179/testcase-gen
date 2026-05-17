"""Flask Web 应用"""

import json
import os
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file, stream_with_context

import db
from generator import generate_testcases
from llm_client import LLMClient, load_config
from output import to_excel, to_markdown
from preferences import compute_diffs, extract_preferences
from reader import (get_image_media_type, image_to_base64, is_image,
                    read_excel, read_text)
from reviewer import optimize_testcases, review_testcases

app = Flask(__name__)

# 初始化数据库
db.init_db()
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32MB（支持多张图片）

OUTPUT_DIR = "./output"


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


def get_generate_client() -> LLMClient:
    config = load_config("config.yaml")
    return build_client(config["generate"])


def get_review_client() -> LLMClient:
    config = load_config("config.yaml")
    review_cfg = config.get("review", {})
    if review_cfg.get("enabled", False):
        return build_client(review_cfg)
    return build_client(config["generate"])


def get_image_client() -> LLMClient | None:
    config = load_config("config.yaml")
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

            config = load_config("config.yaml")
            default_priority = priority or config["testcase"]["default_priority"]
            _case_types = case_types or config["testcase"]["case_types"]
            max_testcases = config["testcase"].get("max_testcases", 100)

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
def api_history():
    """列出历史记录"""
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    sessions = db.list_sessions(limit=limit, offset=offset)
    return jsonify({"sessions": sessions})


@app.route("/api/history/<int:session_id>")
def api_history_detail(session_id):
    """获取单条历史记录"""
    session = db.get_session(session_id)
    if not session:
        return jsonify({"error": "记录不存在"}), 404
    return jsonify(session)


@app.route("/api/history/<int:session_id>", methods=["DELETE"])
def api_history_delete(session_id):
    """删除历史记录"""
    db.delete_session(session_id)
    return jsonify({"success": True})


@app.route("/api/history/<int:session_id>/review", methods=["POST"])
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
def api_preferences():
    """列出所有偏好规则"""
    prefs = db.list_all_preferences()
    return jsonify({"preferences": prefs})


@app.route("/api/preferences/extract", methods=["POST"])
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
def api_preferences_update(pref_id):
    """更新偏好规则（启用/禁用/修改）"""
    data = request.get_json()
    active = data.get("active")
    pattern = data.get("pattern")
    db.update_preference(pref_id, active=active, pattern=pattern)
    return jsonify({"success": True})


@app.route("/api/preferences/<int:pref_id>", methods=["DELETE"])
def api_preferences_delete(pref_id):
    """删除偏好规则"""
    db.delete_preference(pref_id)
    return jsonify({"success": True})
