"""Flask Web 应用"""

import os
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from generator import generate_testcases
from llm_client import LLMClient, load_config
from output import to_excel, to_markdown
from reader import (get_image_media_type, image_to_base64, is_image,
                    read_excel, read_text)
from reviewer import review_testcases

app = Flask(__name__)
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


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """生成测试用例（支持文本 + 图片混合输入）"""
    try:
        requirement = ""
        priority = None
        case_types = None
        images = []

        is_multipart = request.content_type and "multipart/form-data" in request.content_type

        if is_multipart:
            # 文本需求
            requirement = request.form.get("requirement", "")
            priority = request.form.get("priority")
            ct = request.form.get("case_types")
            if ct:
                case_types = [x.strip() for x in ct.split(",") if x.strip()]

            # 处理上传的文件
            files = request.files.getlist("files")
            for f in files:
                if not f.filename:
                    continue
                suffix = Path(f.filename).suffix.lower()
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    f.save(tmp.name)
                    if is_image(tmp.name):
                        # 图片：转 base64
                        images.append({
                            "data": image_to_base64(tmp.name),
                            "media_type": get_image_media_type(tmp.name),
                            "filename": f.filename,
                        })
                    elif suffix in (".xlsx", ".xls"):
                        requirement += "\n" + read_excel(tmp.name)
                    else:
                        requirement += "\n" + read_text(tmp.name)
                    os.unlink(tmp.name)
        else:
            data = request.get_json()
            requirement = data.get("requirement", "")
            priority = data.get("priority")
            case_types = data.get("case_types")

        if not requirement.strip() and not images:
            return jsonify({"error": "需求内容不能为空（文本或图片至少提供一项）"}), 400

        # 获取参数
        config = load_config("config.yaml")
        default_priority = priority or config["testcase"]["default_priority"]
        case_types = case_types or config["testcase"]["case_types"]

        # 生成用例
        client = get_generate_client()
        testcases = generate_testcases(
            client, requirement, default_priority, case_types,
            images=images if images else None,
        )

        # 导出文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        excel_path = to_excel(testcases, OUTPUT_DIR, f"testcases_{timestamp}.xlsx")
        md_path = to_markdown(testcases, OUTPUT_DIR, f"testcases_{timestamp}.md")

        return jsonify({
            "success": True,
            "count": len(testcases),
            "testcases": testcases,
            "files": {
                "excel": excel_path,
                "markdown": md_path,
            },
            "input": {
                "has_text": bool(requirement.strip()),
                "image_count": len(images),
                "image_names": [img["filename"] for img in images],
            }
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/review", methods=["POST"])
def api_review():
    """评审测试用例"""
    try:
        data = request.get_json()
        requirement = data.get("requirement", "")
        testcases = data.get("testcases", [])

        if not testcases:
            return jsonify({"error": "没有可评审的用例"}), 400

        client = get_review_client()
        result = review_testcases(client, requirement, testcases)

        report_path = Path(OUTPUT_DIR) / "review_report.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"# 测试用例评审报告\n\n{result}")

        return jsonify({
            "success": True,
            "review": result,
            "report_path": str(report_path),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/download/<path:filename>")
def api_download(filename):
    """下载文件"""
    filepath = os.path.join(OUTPUT_DIR, filename)
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    return jsonify({"error": "文件不存在"}), 404
