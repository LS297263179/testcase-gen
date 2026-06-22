"""生成相关路由 - 需求分析、用例生成、评审、优化、XMind 转换"""

import json
import logging
import os
import re
import tempfile
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_file, session, stream_with_context

from core import db
from core.llm_client import load_config
from core.output import to_excel, to_markdown, xmind_to_excel
from core.reviewer import optimize_testcases, review_testcases
from web.utils import (OUTPUT_DIR, cleanup_old_output_files, csrf_protect,
                       get_generate_client, get_image_client, get_review_client,
                       get_user_output_dir, login_required, process_uploaded_files, sse_format)

logger = logging.getLogger("web")

bp = Blueprint("generate", __name__)


# ============================================================
# JSON 提取工具（从 LLM 响应中提取 JSON 数组）
# ============================================================

def _extract_json_array(text: str) -> list[dict] | None:
    """从 LLM 响应中提取 JSON 数组（兼容 thinking 模式、markdown 包裹等）"""
    text = text.strip()
    if not text:
        return None

    def _try_parse(s: str) -> list[dict] | None:
        try:
            result = json.loads(s)
            if isinstance(result, list):
                return result
        except (json.JSONDecodeError, ValueError):
            pass
        try:
            import json5
            result = json5.loads(s)
            if isinstance(result, list):
                return result
        except Exception:
            pass
        return None

    # 1. 直接解析
    result = _try_parse(text)
    if result:
        return result

    # 2. 去掉 thinking 模式内容
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<thinking>.*?</thinking>", "", cleaned, flags=re.DOTALL)
    cleaned = cleaned.strip()

    result = _try_parse(cleaned)
    if result:
        return result

    # 3. 提取 ```json ... ``` 或 ``` ... ``` 块
    for m in re.finditer(r"```(?:json)?\s*\n?(.*?)\n?\s*```", cleaned, re.DOTALL):
        result = _try_parse(m.group(1).strip())
        if result:
            return result

    # 4. 找第一个 [ 到最后一个 ]
    for source in (cleaned, text):
        start = source.find("[")
        end = source.rfind("]")
        if start != -1 and end != -1 and end > start:
            result = _try_parse(source[start:end + 1])
            if result:
                return result

    # 5. 正则匹配所有可能的 JSON 数组
    for m in re.finditer(r"\[[\s\S]*?\](?=\s*$|\s*[^}\]])", cleaned):
        result = _try_parse(m.group())
        if result:
            return result

    return None


# ============================================================
# XMind 树描述工具
# ============================================================

def _build_tree_description(sheets: list[dict]) -> str:
    """构建思维导图的文字描述，供 LLM 理解"""
    lines = []
    for sheet in sheets:
        lines.append(f"【{sheet['title']}】")
        _format_node(sheet["root"], lines, indent=0)
    return "\n".join(lines)


def _format_node(node: dict, lines: list, indent: int):
    """递归格式化节点"""
    prefix = "  " * indent + "- "
    lines.append(f"{prefix}{node['title']}")
    for child in node.get("children", []):
        _format_node(child, lines, indent + 1)


# ============================================================
# 需求分析 API
# ============================================================

@bp.route("/api/analyze", methods=["POST"])
@login_required
@csrf_protect
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
            from core.generator import analyze_modules
            client = get_generate_client()
            _ct = case_types or load_config()["testcase"]["case_types"]

            yield sse_format({"type": "progress", "message": "正在分析需求，拆解功能模块..."})
            complexity, modules = analyze_modules(client, requirement, _ct, None)

            if not modules:
                yield sse_format({"type": "done", "data": {
                    "success": True, "complexity": complexity, "modules": [],
                    "message": "模块分析失败，请直接生成"
                }})
                return

            yield sse_format({"type": "done", "data": {
                "success": True,
                "complexity": complexity,
                "modules": modules,
            }})
        except Exception as e:
            logger.exception("SSE 流处理异常")
            yield sse_format({"type": "error", "message": "服务器内部错误，请查看日志详情"})

    return Response(
        stream_with_context(sse_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ============================================================
# 测试用例生成 API
# ============================================================

@bp.route("/api/generate", methods=["POST"])
@login_required
@csrf_protect
def api_generate():
    """生成测试用例（SSE 流式返回进度 + 结果）"""
    priority = None
    case_types = None
    images = []

    is_multipart = request.content_type and "multipart/form-data" in request.content_type

    _VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}
    _VALID_CASE_TYPES = {"功能测试", "边界测试", "异常测试", "兼容性测试", "性能测试", "安全测试", "UI测试"}
    _MAX_REQUIREMENT_LEN = 100_000

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

            images, file_text = process_uploaded_files(request.files.getlist("files"))
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

            from core.generator import (analyze_modules, generate_for_module, deduplicate,
                                       deduplicate_by_steps, generate_all_in_one, limit_testcases)
            client = get_generate_client()
            image_client = get_image_client() if images else None
            active_client = image_client if (images and image_client) else client

            yield sse_format({"type": "progress", "message": "正在分析需求，拆解功能模块..."})
            complexity, modules = analyze_modules(active_client, requirement, _case_types, images if images else None)

            if not modules:
                yield sse_format({"type": "progress", "message": "模块分析失败，使用一次性生成模式..."})
                testcases = generate_all_in_one(active_client, requirement, default_priority, _case_types, images if images else None, max_testcases, pref_context or None)
            else:
                # 按模块并行生成
                from concurrent.futures import ThreadPoolExecutor, as_completed
                total_modules = len(modules)
                max_workers = min(total_modules, 5)
                complexity_label = {"simple": "简单", "medium": "中等", "complex": "复杂"}.get(complexity, "中等")
                yield sse_format({"type": "progress", "message": f"需求复杂度：{complexity_label}，正在并行生成 {total_modules} 个模块的测试用例（{max_workers} 路并发）..."})

                all_testcases = []
                _images = images if images else None
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_module = {
                        executor.submit(generate_for_module, active_client, requirement, mod, default_priority, _images, complexity, pref_context or None): mod
                        for mod in modules
                    }
                    completed = 0
                    for future in as_completed(future_to_module):
                        mod = future_to_module[future]
                        completed += 1
                        try:
                            cases = future.result()
                            all_testcases.extend(cases)
                            yield sse_format({"type": "progress", "message": f"「{mod['name']}」模块完成，生成 {len(cases)} 条用例 ({completed}/{total_modules})"})
                        except Exception as e:
                            yield sse_format({"type": "progress", "message": f"「{mod['name']}」模块生成失败: {e} ({completed}/{total_modules})"})

                if not all_testcases:
                    raise ValueError("分段生成未产出任何用例")

                # 去重编号
                raw_count = len(all_testcases)
                testcases = deduplicate(all_testcases)
                dedup_count = raw_count - len(testcases)
                if dedup_count > 0:
                    yield sse_format({"type": "progress", "message": f"精确去重完成，移除 {dedup_count} 条重复用例"})

                step_dedup_before = len(testcases)
                testcases = deduplicate_by_steps(testcases)
                step_dedup_count = step_dedup_before - len(testcases)
                if step_dedup_count > 0:
                    yield sse_format({"type": "progress", "message": f"步骤语义去重完成，移除 {step_dedup_count} 条相似用例"})

                if len(testcases) > max_testcases:
                    yield sse_format({"type": "progress", "message": f"用例数 ({len(testcases)}) 超过上限 {max_testcases}，按优先级保留"})
                    testcases = limit_testcases(testcases, max_testcases)

                for i, tc in enumerate(testcases):
                    tc["id"] = f"TC_{i + 1:03d}"

            # 导出文件（用户隔离目录 + UUID 文件名）
            user_dir = get_user_output_dir(session.get("user_id"))
            file_id = uuid.uuid4().hex[:12]
            excel_path = to_excel(testcases, user_dir, f"testcases_{file_id}.xlsx")
            md_path = to_markdown(testcases, user_dir, f"testcases_{file_id}.md")

            cleanup_old_output_files()

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
            yield sse_format({"type": "done", "data": result})

        except Exception as e:
            logger.exception("SSE 流处理异常")
            yield sse_format({"type": "error", "message": "服务器内部错误，请查看日志详情"})

    return Response(
        stream_with_context(sse_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ============================================================
# 评审 & 优化 API
# ============================================================

@bp.route("/api/review", methods=["POST"])
@login_required
@csrf_protect
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

            yield sse_format({"type": "progress", "message": f"正在分析 {len(testcases)} 条用例..."})
            result = review_testcases(client, requirement, testcases)

            yield sse_format({"type": "progress", "message": "正在生成评审报告..."})
            report_path = Path(OUTPUT_DIR) / "review_report.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(f"# 测试用例评审报告\n\n{result}")

            yield sse_format({"type": "done", "data": {
                "success": True,
                "review": result,
                "report_path": str(report_path),
            }})
        except Exception as e:
            logger.exception("SSE 流处理异常")
            yield sse_format({"type": "error", "message": "服务器内部错误，请查看日志详情"})

    return Response(
        stream_with_context(sse_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@bp.route("/api/optimize", methods=["POST"])
@login_required
@csrf_protect
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

            yield sse_format({"type": "progress", "message": "正在分析评审报告中的问题和建议..."})
            optimized = optimize_testcases(client, requirement, testcases, review_report)

            yield sse_format({"type": "progress", "message": f"正在导出优化后的 {len(optimized)} 条用例..."})
            user_dir = get_user_output_dir(session.get("user_id"))
            file_id = uuid.uuid4().hex[:12]
            excel_path = to_excel(optimized, user_dir, f"testcases_optimized_{file_id}.xlsx")
            md_path = to_markdown(optimized, user_dir, f"testcases_optimized_{file_id}.md")

            yield sse_format({"type": "done", "data": {
                "success": True,
                "count": len(optimized),
                "testcases": optimized,
                "files": {"excel": excel_path, "markdown": md_path},
            }})
        except Exception as e:
            logger.exception("SSE 流处理异常")
            yield sse_format({"type": "error", "message": "服务器内部错误，请查看日志详情"})

    return Response(
        stream_with_context(sse_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ============================================================
# XMind 转测试用例
# ============================================================

@bp.route("/api/xmind2case", methods=["POST"])
@login_required
@csrf_protect
def api_xmind2case():
    """上传 XMind 文件，通过 LLM 生成测试用例，返回 Excel 下载"""
    from core.xmind_utils import parse_xmind, flatten_topics

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "请上传 XMind 文件"}), 400
    if not file.filename.lower().endswith(".xmind"):
        return jsonify({"error": "文件格式不正确，请上传 .xmind 文件"}), 400

    with tempfile.NamedTemporaryFile(delete=False, suffix=".xmind") as tmp:
        file.save(tmp)
        tmp_path = tmp.name

    def sse_stream():
        try:
            yield sse_format({"type": "progress", "message": "正在解析 XMind 文件..."})
            sheets = parse_xmind(tmp_path)
            if not sheets:
                yield sse_format({"type": "error", "message": "XMind 文件中没有找到内容"})
                return

            all_items = []
            for sheet in sheets:
                root = sheet["root"]
                items = flatten_topics(root)
                all_items.extend(items)

            if not all_items:
                yield sse_format({"type": "error", "message": "XMind 文件中没有找到节点"})
                return

            tree_desc = _build_tree_description(sheets)

            yield sse_format({"type": "progress", "message": f"已解析 {len(all_items)} 个节点，正在调用 AI 生成测试用例..."})

            client = get_generate_client()
            system_prompt = (
                "你是一位资深软件测试工程师。根据用户提供的思维导图结构，生成完整的测试用例列表。\n\n"
                "要求：\n"
                "1. 输出一个 JSON 数组，不要包含任何其他文字、解释、markdown 标记\n"
                "2. 每个用例包含以下字段：\n"
                '   - "module": 所属模块名（从思维导图的层级结构中提取）\n'
                '   - "title": 用例标题（简洁描述测试点）\n'
                '   - "precondition": 前置条件（如无则留空字符串）\n'
                '   - "steps": 操作步骤（用 \\n 分隔多步）\n'
                '   - "expected": 预期结果\n'
                '   - "priority": 优先级（P0/P1/P2/P3，根据功能重要性和风险判断）\n'
                '   - "remark": 备注信息（如无则留空字符串）\n'
                '3. id 字段留空字符串 ""，系统会自动生成\n'
                "4. 优先级判断标准：P0=核心功能/阻塞级，P1=重要功能，P2=一般功能，P3=边缘场景\n"
                "5. 每个叶子节点或测试点至少生成一条用例\n"
                "6. 思维导图格式可能不规范，根据内容合理推断模块、步骤和预期结果\n"
                "7. 直接输出 JSON 数组，以 [ 开头，以 ] 结尾\n\n"
                "示例输出：\n"
                '[{"id":"","module":"登录模块","title":"正确用户名密码登录成功","precondition":"用户已注册","steps":"1. 打开登录页\\n2. 输入用户名\\n3. 输入密码\\n4. 点击登录","expected":"跳转到首页","priority":"P0","remark":""}]'
            )
            user_prompt = f"请根据以下思维导图结构生成测试用例：\n\n{tree_desc}"

            response = client.chat(system_prompt, user_prompt)
            logger.warning(f"XMind LLM 原始响应（前500字）: {response[:500]}")
            testcases = _extract_json_array(response)
            if not testcases:
                logger.warning("首次 JSON 解析失败，重试中...")
                retry_prompt = (
                    "你的上一次回复无法解析为 JSON 数组。请严格只输出 JSON 数组，不要包含任何解释文字。\n\n"
                    f"以下是思维导图内容：\n{tree_desc}"
                )
                response = client.chat(system_prompt, retry_prompt)
                logger.warning(f"XMind 重试 LLM 响应（前500字）: {response[:500]}")
                testcases = _extract_json_array(response)
            if not testcases:
                logger.warning(f"XMind JSON 解析失败，完整响应: {response[:1000]}")
                yield sse_format({"type": "error", "message": "AI 返回的数据格式不正确，请重试"})
                return

            for i, tc in enumerate(testcases, 1):
                tc["id"] = f"test_{i:03d}"

            yield sse_format({"type": "progress", "message": f"已生成 {len(testcases)} 条用例，正在导出 Excel..."})

            user_dir = get_user_output_dir(session.get("user_id"))
            file_id = uuid.uuid4().hex[:12]
            filepath = xmind_to_excel(testcases, user_dir, f"xmind_cases_{file_id}.xlsx")
            filename = os.path.basename(filepath)

            yield sse_format({"type": "done", "data": {
                "filename": filename,
                "count": len(testcases),
                "testcases": testcases,
            }})

        except Exception as e:
            logger.error(f"XMind 转换失败: {e}\n{traceback.format_exc()}")
            yield sse_format({"type": "error", "message": f"转换失败: {str(e)}"})
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    return Response(stream_with_context(sse_stream()), mimetype="text/event-stream")


@bp.route("/api/xmind-template")
@login_required
def api_xmind_template():
    """下载 XMind 参考模板"""
    import importlib
    import core.xmind_utils as xmind_utils
    importlib.reload(xmind_utils)
    from core.xmind_utils import generate_template

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"测试用例模板_{timestamp}.xmind"
    filepath = os.path.join(OUTPUT_DIR, filename)
    generate_template(filepath)
    logger.warning(f"[DEBUG] 模板已生成: {filepath}, 函数模块: {xmind_utils.__file__}")
    resp = send_file(filepath, as_attachment=True, download_name=filename)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp
