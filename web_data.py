"""数据 CRUD 路由 - 项目资料、历史记录、测试点、偏好规则"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_file, session, stream_with_context

import db
from output import to_excel, to_markdown
from preferences import compute_diffs, extract_preferences
from web_utils import (OUTPUT_DIR, cleanup_old_output_files, csrf_protect,
                       get_generate_client, get_image_client, get_user_output_dir,
                       login_required, process_uploaded_files, sse_format)

logger = logging.getLogger("web")

bp = Blueprint("data", __name__)


# ============================================================
# 项目资料 API
# ============================================================

@bp.route("/api/materials", methods=["GET"])
@login_required
def api_materials_list():
    """列出当前用户的项目资料"""
    materials = db.list_materials(session["user_id"])
    return jsonify({"materials": materials})


@bp.route("/api/materials", methods=["POST"])
@login_required
@csrf_protect
def api_materials_create():
    """创建项目资料（支持图片上传）"""

    title = request.form.get("title", "").strip()
    content = request.form.get("content", "")
    if not title:
        return jsonify({"error": "标题不能为空"}), 400

    images, _ = process_uploaded_files(request.files.getlist("images"))
    mid = db.create_material(session["user_id"], title, content, images)
    return jsonify({"success": True, "id": mid})


@bp.route("/api/materials/<int:mid>", methods=["GET"])
@login_required
def api_materials_get(mid):
    """获取单条项目资料"""
    m = db.get_material(mid)
    if not m or m["user_id"] != session["user_id"]:
        return jsonify({"error": "资料不存在"}), 404
    return jsonify(m)


@bp.route("/api/materials/<int:mid>", methods=["DELETE"])
@login_required
@csrf_protect
def api_materials_delete(mid):
    """删除项目资料"""
    m = db.get_material(mid)
    if not m or m["user_id"] != session["user_id"]:
        return jsonify({"error": "资料不存在"}), 404
    db.delete_material(mid)
    return jsonify({"success": True})


@bp.route("/api/materials/<int:mid>", methods=["PUT"])
@login_required
@csrf_protect
def api_materials_update(mid):
    """更新项目资料"""

    m = db.get_material(mid)
    if not m or m["user_id"] != session["user_id"]:
        return jsonify({"error": "资料不存在"}), 404

    title = request.form.get("title", "").strip()
    content = request.form.get("content", "")
    if not title:
        return jsonify({"error": "标题不能为空"}), 400

    images, _ = process_uploaded_files(request.files.getlist("images"))
    keep_ids_raw = request.form.get("keep_image_ids")
    keep_image_ids = None
    if keep_ids_raw:
        try:
            keep_image_ids = [int(x) for x in json.loads(keep_ids_raw)]
        except (json.JSONDecodeError, ValueError):
            pass
    db.update_material(mid, title, content, images, keep_image_ids)
    return jsonify({"success": True})


# ============================================================
# 测试点 API
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


@bp.route("/api/generate-points", methods=["POST"])
@login_required
@csrf_protect
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
            images, file_text = process_uploaded_files(request.files.getlist("files"))
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

            yield sse_format({"type": "progress", "message": "正在分析需求，生成测试点..."})
            response = active_client.chat("你是一位资深测试专家。", prompt, images=images if images else None)
            text = response.strip()

            import re
            json_match = re.search(r'\[[\s\S]*\]', text)
            if json_match:
                points = json.loads(json_match.group())
            else:
                points = json.loads(text)

            total = sum(len(m.get("points", [])) for m in points)
            title = (requirement or "测试点").strip()[:30]
            tp_id = db.save_test_points(session["user_id"], title, requirement, points, total)

            yield sse_format({"type": "done", "data": {
                "success": True,
                "points": points,
                "total": total,
                "tp_id": tp_id,
            }})
        except Exception as e:
            logger.exception("SSE 流处理异常")
            yield sse_format({"type": "error", "message": "服务器内部错误，请查看日志详情"})

    return Response(
        stream_with_context(sse_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@bp.route("/api/export-points", methods=["POST"])
@login_required
@csrf_protect
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
        return jsonify({"error": "XMind 导出暂不可用，请使用 Markdown 格式导出"}), 501

    return jsonify({"error": "不支持的格式"}), 400


@bp.route("/api/test-points", methods=["GET"])
@login_required
def api_test_points_list():
    """列出当前用户的测试点记录"""
    points = db.list_test_points(session["user_id"])
    return jsonify({"test_points": points})


@bp.route("/api/test-points/<int:tp_id>", methods=["GET"])
@login_required
def api_test_points_get(tp_id):
    """获取单条测试点记录"""
    tp = db.get_test_points(tp_id)
    if not tp or tp["user_id"] != session["user_id"]:
        return jsonify({"error": "记录不存在"}), 404
    return jsonify(tp)


@bp.route("/api/test-points/<int:tp_id>", methods=["DELETE"])
@login_required
@csrf_protect
def api_test_points_delete(tp_id):
    """删除测试点记录"""
    tp = db.get_test_points(tp_id)
    if not tp or tp["user_id"] != session["user_id"]:
        return jsonify({"error": "记录不存在"}), 404
    db.delete_test_points(tp_id)
    return jsonify({"success": True})


# ============================================================
# 历史记录 API
# ============================================================

@bp.route("/api/history")
@login_required
def api_history():
    """列出历史记录"""
    limit = min(request.args.get("limit", 50, type=int), 200)
    offset = max(request.args.get("offset", 0, type=int), 0)
    sessions = db.list_sessions(limit=limit, offset=offset, user_id=session["user_id"])
    return jsonify({"sessions": sessions})


@bp.route("/api/history/<int:session_id>")
@login_required
def api_history_detail(session_id):
    """获取单条历史记录"""
    record = db.get_session(session_id)
    if not record or record.get("user_id") != session["user_id"]:
        return jsonify({"error": "记录不存在"}), 404
    return jsonify(record)


@bp.route("/api/history/<int:session_id>", methods=["DELETE"])
@login_required
@csrf_protect
def api_history_delete(session_id):
    """删除历史记录"""
    record = db.get_session(session_id)
    if not record or record.get("user_id") != session["user_id"]:
        return jsonify({"error": "记录不存在"}), 404
    db.delete_session(session_id)
    return jsonify({"success": True})


@bp.route("/api/history/<int:session_id>/review", methods=["POST"])
@login_required
@csrf_protect
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

@bp.route("/api/preferences")
@login_required
def api_preferences():
    """列出所有偏好规则"""
    prefs = db.list_all_preferences(user_id=session.get("user_id"))
    return jsonify({"preferences": prefs})


@bp.route("/api/preferences/extract", methods=["POST"])
@login_required
@csrf_protect
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
            yield sse_format({"type": "progress", "message": "正在分析修改差异..."})
            diffs = compute_diffs(original, edited)
            if not diffs:
                yield sse_format({"type": "done", "data": {
                    "success": True,
                    "preferences": [],
                    "message": "未检测到有效修改",
                }})
                return

            yield sse_format({"type": "progress", "message": f"检测到 {len(diffs)} 处修改，正在提取偏好规则..."})
            client = get_generate_client()
            prefs = extract_preferences(diffs, client)

            if prefs and session_id:
                db.save_preferences(prefs, session_id, source_diffs=diffs,
                                    user_id=session.get("user_id"))

            yield sse_format({"type": "done", "data": {
                "success": True,
                "preferences": prefs,
                "count": len(prefs),
            }})
        except Exception as e:
            logger.exception("SSE 流处理异常")
            yield sse_format({"type": "error", "message": "服务器内部错误，请查看日志详情"})

    return Response(
        stream_with_context(sse_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@bp.route("/api/preferences/<int:pref_id>", methods=["PUT"])
@login_required
@csrf_protect
def api_preferences_update(pref_id):
    """更新偏好规则（启用/禁用/修改）"""
    pref = db.get_preference(pref_id)
    if not pref:
        return jsonify({"error": "规则不存在"}), 404
    if pref.get("user_id") is not None and pref.get("user_id") != session.get("user_id"):
        return jsonify({"error": "无权操作"}), 403
    data = request.get_json()
    active = data.get("active")
    pattern = data.get("pattern")
    db.update_preference(pref_id, active=active, pattern=pattern)
    return jsonify({"success": True})


@bp.route("/api/preferences/<int:pref_id>", methods=["DELETE"])
@login_required
@csrf_protect
def api_preferences_delete(pref_id):
    """删除偏好规则"""
    pref = db.get_preference(pref_id)
    if not pref:
        return jsonify({"error": "规则不存在"}), 404
    if pref.get("user_id") is not None and pref.get("user_id") != session.get("user_id"):
        return jsonify({"error": "无权操作"}), 403
    db.delete_preference(pref_id)
    return jsonify({"success": True})


# ============================================================
# 文件下载
# ============================================================

@bp.route("/api/download/<path:filename>")
@login_required
def api_download(filename):
    """下载文件（优先用户目录，fallback 共享目录，最后从历史记录重新生成）"""
    user_dir = get_user_output_dir(session.get("user_id"))
    output_real = os.path.realpath(OUTPUT_DIR)

    # 优先从用户专属目录查找
    filepath = os.path.realpath(os.path.join(user_dir, filename))
    if filepath.startswith(os.path.realpath(user_dir) + os.sep) and os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)

    # fallback 到共享目录（兼容旧文件）
    filepath = os.path.realpath(os.path.join(OUTPUT_DIR, filename))
    if not filepath.startswith(output_real + os.sep) and filepath != output_real:
        return jsonify({"error": "非法路径"}), 403

    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)

    # 从历史记录重新生成
    session_id = request.args.get("session_id")
    if session_id:
        try:
            record = db.get_session(int(session_id))
            if record and record.get("user_id") == session.get("user_id"):
                testcases = record.get("testcases", [])
                if testcases:
                    os.makedirs(user_dir, exist_ok=True)
                    if filename.endswith(".xlsx"):
                        to_excel(testcases, user_dir, filename)
                    elif filename.endswith(".md"):
                        to_markdown(testcases, user_dir, filename)
                    regenerated = os.path.join(user_dir, filename)
                    if os.path.exists(regenerated):
                        return send_file(regenerated, as_attachment=True)
        except Exception as e:
            logger.warning(f"重新生成文件失败: {e}")

    return jsonify({"error": "文件不存在，请重新生成"}), 404
