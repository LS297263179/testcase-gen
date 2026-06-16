"""全功能回归测试 - 覆盖所有核心功能路径

测试维度：
1. 配置管理（config.py）
2. 数据库层（db.py）完整 CRUD + 边界
3. 生成器核心逻辑（generator.py）
4. LLM 客户端（llm_client.py）
5. 输出模块（output.py）
6. 读取器（reader.py）
7. 评审器（reviewer.py）
8. 偏好模块（preferences.py）
9. XMind 工具（xmind_utils.py）
10. Web API 端到端流程
11. CLI 入口（main.py）
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ============================================================
# 1. 配置管理
# ============================================================

class TestConfig:
    """config.py 回归测试"""

    def test_load_yaml_config_returns_dict(self):
        from config import load_yaml_config
        cfg = load_yaml_config()
        assert isinstance(cfg, dict)
        # 应包含 generate 和 testcase 段
        assert "generate" in cfg or "testcase" in cfg

    def test_get_testcase_config_has_defaults(self):
        from config import get_testcase_config
        cfg = get_testcase_config()
        assert "default_priority" in cfg
        assert "max_testcases" in cfg
        assert "case_types" in cfg
        assert cfg["default_priority"] in ("P0", "P1", "P2", "P3")

    def test_get_output_config(self):
        from config import get_output_config
        cfg = get_output_config()
        assert "dir" in cfg
        assert "format" in cfg

    def test_get_secret_key_returns_bytes_or_str(self):
        from config import get_secret_key
        key = get_secret_key()
        assert isinstance(key, (bytes, str))
        assert len(key) > 0

    def test_get_secret_key_env_override(self, monkeypatch):
        from config import get_secret_key
        monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret-123")
        key = get_secret_key()
        assert key == "test-secret-123"

    def test_get_model_config_returns_structure(self, tmp_db):
        from config import get_model_config
        cfg = get_model_config()
        assert "generate" in cfg
        assert "review" in cfg

    def test_get_model_config_from_db(self, tmp_db):
        from config import get_model_config
        import db
        # 保存到数据库
        db.save_model_config({
            "generate": {"api_key": "test-key", "model": "test-model", "base_url": "http://test"},
            "review": {"enabled": False},
        })
        cfg = get_model_config()
        assert cfg["generate"]["model"] == "test-model"
        assert cfg["generate"]["api_key"] == "test-key"


# ============================================================
# 2. 数据库层完整测试
# ============================================================

class TestDatabaseFull:
    """db.py 全功能回归"""

    def test_user_lifecycle(self, tmp_db):
        """用户完整生命周期：创建→验证→查询"""
        uid = tmp_db.create_user("regression_user", "pass123456")
        assert uid > 0

        user = tmp_db.verify_user("regression_user", "pass123456")
        assert user is not None
        assert user["id"] == uid

        user_by_id = tmp_db.get_user_by_id(uid)
        assert user_by_id["username"] == "regression_user"

    def test_session_with_images(self, tmp_db):
        """Session 带图片的完整流程"""
        uid = tmp_db.create_user("img_user", "pass123456")
        images = [
            {"data": "base64data1", "media_type": "image/png", "filename": "a.png"},
            {"data": "base64data2", "media_type": "image/jpeg", "filename": "b.jpg"},
        ]
        sid = tmp_db.create_session(
            requirement="带图片的需求",
            testcases=[{"id": "TC_001", "title": "test"}],
            images=images,
            user_id=uid,
        )
        record = tmp_db.get_session(sid)
        assert len(record["images"]) == 2
        assert record["images"][0]["filename"] == "a.png"

    def test_session_pagination(self, tmp_db):
        """Session 分页查询"""
        uid = tmp_db.create_user("page_user", "pass123456")
        for i in range(15):
            tmp_db.create_session(
                requirement=f"需求{i}",
                testcases=[{"id": f"TC_{i}"}],
                user_id=uid,
            )
        page1 = tmp_db.list_sessions(limit=5, offset=0, user_id=uid)
        page2 = tmp_db.list_sessions(limit=5, offset=5, user_id=uid)
        page3 = tmp_db.list_sessions(limit=5, offset=10, user_id=uid)
        assert len(page1) == 5
        assert len(page2) == 5
        assert len(page3) == 5
        # 每页 ID 不同
        ids1 = {s["id"] for s in page1}
        ids2 = {s["id"] for s in page2}
        assert ids1.isdisjoint(ids2)

    def test_materials_with_image_update(self, tmp_db):
        """项目资料图片更新"""
        uid = tmp_db.create_user("mat_user", "pass123456")
        mid = tmp_db.create_material(uid, "资料", "内容", images=[
            {"data": "img1", "media_type": "image/png", "filename": "a.png"},
        ])
        # 获取并确认有 1 张图
        m = tmp_db.get_material(mid)
        assert len(m["images"]) == 1

        # 更新：保留旧图 + 添加新图
        tmp_db.update_material(mid, "资料v2", "新内容", images=[
            {"data": "img2", "media_type": "image/png", "filename": "b.png"},
        ], keep_image_ids=[m["images"][0]["id"]])
        m2 = tmp_db.get_material(mid)
        assert len(m2["images"]) == 2
        assert m2["title"] == "资料v2"

    def test_preference_decay_chain(self, tmp_db):
        """偏好衰减链：连续插入同 category，旧规则权重递减"""
        uid = tmp_db.create_user("pref_user", "pass123456")
        sid = tmp_db.create_session(requirement="r", testcases=[{"id": "T"}], user_id=uid)

        for i in range(5):
            tmp_db.save_preferences(
                [{"category": "step_style", "pattern": f"规则{i}"}],
                sid, user_id=uid,
            )

        prefs = tmp_db.get_active_preferences(limit=10, user_id=uid)
        # 最新的规则权重应为 1.0
        latest = [p for p in prefs if p["pattern"] == "规则4"]
        assert len(latest) == 1
        assert latest[0]["weight"] == 1.0

    def test_model_config_encrypt_decrypt(self, tmp_db):
        """模型配置保存→读取→API Key 解密"""
        original_key = "sk-very-secret-api-key-12345"
        tmp_db.save_model_config({
            "generate": {"api_key": original_key, "model": "test", "base_url": "http://t"},
            "review": {"enabled": False},
        })
        cfg = tmp_db.get_model_config()
        assert cfg["generate"]["api_key"] == original_key

    def test_test_points_crud(self, tmp_db):
        """测试点完整 CRUD"""
        uid = tmp_db.create_user("tp_user", "pass123456")
        points = [{"module": "登录", "points": [{"title": "测试点1", "description": "描述"}]}]
        tp_id = tmp_db.save_test_points(uid, "测试点标题", "需求内容", points, 1)
        assert tp_id > 0

        tp = tmp_db.get_test_points(tp_id)
        assert tp["title"] == "测试点标题"
        assert tp["points"][0]["module"] == "登录"

        tps = tmp_db.list_test_points(uid)
        assert len(tps) == 1

        tmp_db.delete_test_points(tp_id)
        assert tmp_db.get_test_points(tp_id) is None


# ============================================================
# 3. 生成器核心逻辑
# ============================================================

class TestGeneratorFull:
    """generator.py 全功能回归"""

    def test_parse_response_all_formats(self):
        """测试所有 JSON 格式的解析"""
        from generator import parse_response

        cases = [
            # 标准格式
            '{"testcases": [{"id":"TC_001","title":"t","module":"m","steps":"s","expected":"e","priority":"P1","type":"功能测试"}]}',
            # 带 markdown 包裹
            '```json\n{"testcases": [{"id":"TC_001","title":"t","module":"m","steps":"s","expected":"e","priority":"P1","type":"功能测试"}]}\n```',
            # 带前后文字
            '好的，这是用例：\n{"testcases": [{"id":"TC_001","title":"t","module":"m","steps":"s","expected":"e","priority":"P1","type":"功能测试"}]}\n希望有帮助！',
            # 带 thinking 标签
            '<think>分析中</think>\n```json\n{"testcases": [{"id":"TC_001","title":"t","module":"m","steps":"s","expected":"e","priority":"P1","type":"功能测试"}]}\n```',
        ]
        for raw in cases:
            result = parse_response(raw)
            assert len(result) == 1, f"Failed to parse: {raw[:50]}..."

    def test_deduplicate_mixed(self):
        """混合去重：精确 + 语义"""
        from generator import deduplicate, deduplicate_by_steps

        cases = [
            {"id": "TC_001", "module": "m", "title": "登录成功", "steps": "1. 输入账号\n2. 输入密码\n3. 点击登录", "expected": "成功", "priority": "P1", "type": "功能测试"},
            {"id": "TC_002", "module": "m", "title": "登录成功", "steps": "1. 输入账号\n2. 输入密码\n3. 点击登录", "expected": "成功", "priority": "P1", "type": "功能测试"},  # 完全重复
            {"id": "TC_003", "module": "m", "title": "正常登录", "steps": "1. 输入账号\n2. 输入密码\n3. 点击登录按钮", "expected": "登录成功", "priority": "P1", "type": "功能测试"},  # 语义重复
            {"id": "TC_004", "module": "m", "title": "密码错误", "steps": "1. 输入账号\n2. 输入错误密码\n3. 点击登录", "expected": "提示错误", "priority": "P1", "type": "异常测试"},  # 不同
        ]

        after_exact = deduplicate(cases)
        assert len(after_exact) == 3  # TC_002 被精确去重

        after_step = deduplicate_by_steps(after_exact, threshold=0.5)
        assert len(after_step) == 2  # TC_003 被语义去重

    def test_limit_preserves_order(self):
        """限制数量时保持同优先级的原始顺序"""
        from generator import limit_testcases

        cases = [
            {"id": "TC_001", "priority": "P1", "title": "a", "module": "m", "steps": "s", "expected": "e", "type": "t"},
            {"id": "TC_002", "priority": "P1", "title": "b", "module": "m", "steps": "s", "expected": "e", "type": "t"},
            {"id": "TC_003", "priority": "P0", "title": "c", "module": "m", "steps": "s", "expected": "e", "type": "t"},
            {"id": "TC_004", "priority": "P1", "title": "d", "module": "m", "steps": "s", "expected": "e", "type": "t"},
        ]
        result = limit_testcases(cases, 3)
        assert len(result) == 3
        # P0 应排第一
        assert result[0]["id"] == "TC_003"

    def test_validate_completes_all_fields(self):
        """验证字段补全的完整性"""
        from generator import validate_testcases

        raw = [{"title": "只有标题"}]
        result = validate_testcases(raw)
        tc = result[0]
        assert tc["id"] == "TC_001"
        assert tc["module"] == "未分类"
        assert tc["precondition"] == ""
        assert tc["steps"] == ""
        assert tc["expected"] == ""
        assert tc["priority"] == "P1"
        assert tc["type"] == "功能测试"

    def test_step_fingerprint_chinese(self):
        """中文步骤的指纹提取"""
        from generator import _extract_step_fingerprint

        fp = _extract_step_fingerprint("1. 打开登录页面\n2. 输入手机号 13800138000\n3. 点击获取验证码\n4. 输入验证码\n5. 点击登录按钮")
        verbs = {"打开", "输入", "点击"}
        found = set()
        for f in fp:
            for v in verbs:
                if v in f:
                    found.add(v)
        assert found == verbs


# ============================================================
# 4. LLM 客户端
# ============================================================

class TestLLMClient:
    """llm_client.py 回归测试"""

    def test_build_client_openai(self):
        from llm_client import build_client
        client = build_client({
            "api_type": "openai",
            "base_url": "http://localhost:8080/v1",
            "api_key": "test-key",
            "model": "test-model",
        })
        assert client.model == "test-model"
        assert client.api_type == "openai"

    def test_build_client_anthropic(self):
        from llm_client import build_client
        client = build_client({
            "api_type": "anthropic",
            "base_url": "https://api.anthropic.com",
            "api_key": "test-key",
            "model": "claude-3",
        })
        assert client.model == "claude-3"
        assert client.api_type == "anthropic"

    def test_load_config(self):
        from llm_client import load_config
        cfg = load_config()
        assert isinstance(cfg, dict)

    def test_chat_stream_method_exists(self):
        from llm_client import LLMClient
        assert hasattr(LLMClient, "chat_stream")

    def test_usage_stats_dataclass(self):
        from llm_client import UsageStats
        stats = UsageStats(prompt_tokens=100, completion_tokens=50)
        assert stats.prompt_tokens == 100
        assert stats.completion_tokens == 50


# ============================================================
# 5. 输出模块
# ============================================================

class TestOutput:
    """output.py 回归测试"""

    def test_to_excel_creates_file(self, tmp_path):
        from output import to_excel
        cases = [
            {"id": "TC_001", "module": "m", "title": "t", "precondition": "p",
             "steps": "s", "expected": "e", "priority": "P1", "type": "功能测试"},
        ]
        path = to_excel(cases, str(tmp_path), "test.xlsx")
        assert os.path.exists(path)
        assert path.endswith(".xlsx")

    def test_to_markdown_creates_file(self, tmp_path):
        from output import to_markdown
        cases = [
            {"id": "TC_001", "module": "m", "title": "t", "precondition": "p",
             "steps": "s", "expected": "e", "priority": "P1", "type": "功能测试"},
        ]
        path = to_markdown(cases, str(tmp_path), "test.md")
        assert os.path.exists(path)
        content = open(path, encoding="utf-8").read()
        assert "TC_001" in content
        assert "t" in content

    def test_xmind_to_excel(self, tmp_path):
        from output import xmind_to_excel
        cases = [
            {"id": "TC_001", "module": "登录", "title": "测试", "precondition": "",
             "steps": "步骤", "expected": "结果", "priority": "P1", "remark": ""},
        ]
        path = xmind_to_excel(cases, str(tmp_path), "xmind_out.xlsx")
        assert os.path.exists(path)

    def test_normalize_steps(self):
        from output import _normalize_steps
        result = _normalize_steps("1.步骤一\n2.步骤二\n3.步骤三")
        assert "1." in result
        assert "2." in result

    def test_strip_trailing_punctuation(self):
        from output import _strip_trailing_punctuation
        assert _strip_trailing_punctuation("测试。") == "测试"
        assert _strip_trailing_punctuation("测试.") == "测试"
        assert _strip_trailing_punctuation("测试") == "测试"


# ============================================================
# 6. 读取器
# ============================================================

class TestReader:
    """reader.py 回归测试"""

    def test_read_text_markdown(self, tmp_path):
        from reader import read_text
        f = tmp_path / "test.md"
        f.write_text("# 需求文档\n\n用户登录功能", encoding="utf-8")
        result = read_text(str(f))
        assert "需求文档" in result
        assert "用户登录" in result

    def test_read_text_plain(self, tmp_path):
        from reader import read_text
        f = tmp_path / "test.txt"
        f.write_text("纯文本文档内容", encoding="utf-8")
        result = read_text(str(f))
        assert "纯文本" in result

    def test_is_image_detection(self, tmp_path):
        from reader import is_image
        # 创建一个假的 PNG 文件头
        png_file = tmp_path / "test.png"
        png_file.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100)
        assert is_image(str(png_file)) is True

        txt_file = tmp_path / "test.txt"
        txt_file.write_text("not an image")
        assert is_image(str(txt_file)) is False

    def test_get_image_media_type(self, tmp_path):
        from reader import get_image_media_type
        for ext, mime in [("png", "image/png"), ("jpg", "image/jpeg"), ("gif", "image/gif"), ("webp", "image/webp")]:
            f = tmp_path / f"test.{ext}"
            f.write_bytes(b'\x00' * 10)
            assert get_image_media_type(str(f)) == mime


# ============================================================
# 7. 偏好模块
# ============================================================

class TestPreferences:
    """preferences.py 回归测试"""

    def test_compute_diffs(self):
        from preferences import compute_diffs
        original = [
            {"id": "TC_001", "title": "旧标题", "steps": "旧步骤", "expected": "旧预期", "priority": "P1", "type": "功能测试", "precondition": ""},
        ]
        edited = [
            {"id": "TC_001", "title": "新标题", "steps": "旧步骤", "expected": "新预期", "priority": "P1", "type": "功能测试", "precondition": ""},
        ]
        diffs = compute_diffs(original, edited)
        assert len(diffs) == 1
        assert "title" in diffs[0]["field_diffs"]
        assert "expected" in diffs[0]["field_diffs"]
        assert "steps" not in diffs[0]["field_diffs"]

    def test_compute_diffs_no_change(self):
        from preferences import compute_diffs
        tc = {"id": "TC_001", "title": "t", "steps": "s", "expected": "e", "priority": "P1", "type": "t", "precondition": ""}
        diffs = compute_diffs([tc], [tc])
        assert len(diffs) == 0


# ============================================================
# 8. XMind 工具
# ============================================================

class TestXmindUtils:
    """xmind_utils.py 回归测试"""

    def test_generate_template_creates_file(self, tmp_path):
        from xmind_utils import generate_template
        path = str(tmp_path / "template.xmind")
        generate_template(path)
        assert os.path.exists(path)
        assert os.path.getsize(path) > 0

    def test_flatten_topics(self):
        from xmind_utils import flatten_topics
        root = {
            "title": "根节点",
            "children": [
                {"title": "子节点1", "children": [
                    {"title": "孙节点1"},
                ]},
                {"title": "子节点2"},
            ],
        }
        items = flatten_topics(root)
        titles = [item["title"] for item in items]
        assert "子节点1" in titles
        assert "孙节点1" in titles
        assert "子节点2" in titles


# ============================================================
# 9. Web API 端到端流程
# ============================================================

class TestWebE2E:
    """Web API 端到端回归测试"""

    def test_full_auth_flow(self, client):
        """完整认证流程：注册→登出→登录→访问→登出"""
        # 注册
        rv = client.post("/api/register", json={"username": "e2e_user", "password": "pass123456"})
        assert rv.status_code == 200
        csrf = rv.get_json()["csrf_token"]

        # 确认已登录
        rv = client.get("/api/me")
        assert rv.get_json()["logged_in"] is True

        # 登出
        rv = client.post("/api/logout")
        assert rv.status_code == 200

        # 确认已登出
        rv = client.get("/api/me")
        assert rv.get_json()["logged_in"] is False

        # 重新登录
        rv = client.post("/api/login", json={"username": "e2e_user", "password": "pass123456"})
        assert rv.status_code == 200

    def test_materials_full_crud(self, auth_client):
        """项目资料完整 CRUD"""
        csrf = _get_csrf(auth_client)

        # 创建
        rv = auth_client.post("/api/materials", data={"title": "测试资料", "content": "内容"},
                              headers={"X-CSRF-Token": csrf}, content_type="multipart/form-data")
        assert rv.status_code == 200
        mid = rv.get_json()["id"]

        # 列表
        rv = auth_client.get("/api/materials")
        assert rv.status_code == 200
        assert len(rv.get_json()["materials"]) >= 1

        # 获取详情
        rv = auth_client.get(f"/api/materials/{mid}")
        assert rv.status_code == 200
        assert rv.get_json()["title"] == "测试资料"

        # 更新
        rv = auth_client.put(f"/api/materials/{mid}", data={"title": "更新后", "content": "新内容"},
                             headers={"X-CSRF-Token": csrf}, content_type="multipart/form-data")
        assert rv.status_code == 200

        # 确认更新
        rv = auth_client.get(f"/api/materials/{mid}")
        assert rv.get_json()["title"] == "更新后"

        # 删除
        rv = auth_client.delete(f"/api/materials/{mid}", headers={"X-CSRF-Token": csrf})
        assert rv.status_code == 200

        # 确认删除
        rv = auth_client.get(f"/api/materials/{mid}")
        assert rv.status_code == 404

    def test_model_config_flow(self, auth_client):
        """模型配置完整流程"""
        # 获取预设列表
        rv = auth_client.get("/api/model-presets")
        assert rv.status_code == 200
        presets = rv.get_json()["presets"]
        assert "mimo" in presets

        # 获取当前配置
        rv = auth_client.get("/api/model-config")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "config" in data
        assert "presets" in data

    def test_dashboard_stats(self, auth_client):
        """仪表盘统计"""
        rv = auth_client.get("/api/dashboard")
        assert rv.status_code == 200
        stats = rv.get_json()
        assert "total_sessions" in stats
        assert "total_testcases" in stats
        assert "current_model" in stats

    def test_history_empty(self, auth_client):
        """空历史记录"""
        rv = auth_client.get("/api/history")
        assert rv.status_code == 200
        assert rv.get_json()["sessions"] == []

    def test_preferences_empty(self, auth_client):
        """空偏好列表"""
        rv = auth_client.get("/api/preferences")
        assert rv.status_code == 200
        assert rv.get_json()["preferences"] == []

    def test_health_endpoint(self, client):
        """健康检查"""
        rv = client.get("/api/health")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["status"] == "ok"
        assert data["database"] == "ok"

    def test_index_page(self, client):
        """首页返回 HTML"""
        rv = client.get("/")
        assert rv.status_code == 200
        assert b"<!DOCTYPE html>" in rv.data or b"<html" in rv.data

    def test_404_api(self, client):
        """不存在的 API 返回 JSON 404"""
        rv = client.get("/api/nonexistent")
        assert rv.status_code == 404
        assert "error" in rv.get_json()

    def test_protected_endpoints_require_login(self, client):
        """受保护接口未登录返回 401"""
        endpoints = ["/api/dashboard", "/api/materials", "/api/history", "/api/preferences", "/api/model-config"]
        for ep in endpoints:
            rv = client.get(ep)
            assert rv.status_code == 401, f"{ep} should return 401"

    def test_csrf_protection(self, auth_client):
        """CSRF 保护：不带 token 的 POST 被拒"""
        rv = auth_client.post("/api/materials", data={"title": "t"}, content_type="multipart/form-data")
        assert rv.status_code == 403

    def test_rate_limit_on_register(self, client):
        """注册接口速率限制"""
        import web_utils
        web_utils._rate_limit_store.clear()
        web_utils._rate_limit_last_cleanup = 0.0

        # 正常注册前 10 次应该成功
        for i in range(10):
            rv = client.post("/api/register", json={"username": f"rate_user_{i}", "password": "pass123456"})
            assert rv.status_code == 200, f"Register {i} failed: {rv.get_json()}"

        # 第 11 次应该被限流
        rv = client.post("/api/register", json={"username": "rate_user_overflow", "password": "pass123456"})
        assert rv.status_code == 429

    def test_download_nonexistent(self, auth_client):
        """下载不存在的文件返回 404"""
        rv = auth_client.get("/api/download/nonexistent_file.xlsx")
        assert rv.status_code == 404

    def test_download_path_traversal(self, auth_client):
        """路径遍历攻击被拦截"""
        rv = auth_client.get("/api/download/../../etc/passwd")
        assert rv.status_code in (403, 404)


# ============================================================
# 10. LLM Client 流式方法
# ============================================================

class TestLLMStream:
    """LLM 流式输出回归"""

    def test_chat_stream_is_generator(self):
        """chat_stream 返回生成器"""
        from llm_client import LLMClient
        from unittest.mock import MagicMock

        # 直接构造 LLMClient 并 mock 内部 client
        llm = object.__new__(LLMClient)
        llm.api_type = "openai"
        llm.model = "test"
        llm.temperature = 0.3
        llm.max_tokens = 4096
        llm.max_retries = 1
        llm.enable_thinking = False

        mock_chunk = MagicMock()
        mock_chunk.choices = [MagicMock()]
        mock_chunk.choices[0].delta.content = "hello"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = [mock_chunk]
        llm.client = mock_client

        chunks = list(llm.chat_stream("sys", "user"))
        assert chunks == ["hello"]


# ============================================================
# 工具函数
# ============================================================

def _get_csrf(client):
    """从 session 中获取 CSRF token"""
    with client.session_transaction() as sess:
        return sess.get("csrf_token", "")
