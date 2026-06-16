"""Pytest 共享 fixtures"""

import json
import os
import sys
import tempfile

import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def tmp_db(tmp_path):
    """创建临时数据库，测试结束后自动清理"""
    import db
    db_path = str(tmp_path / "test.db")
    old_path = db._DB_PATH
    db.set_db_path(db_path)
    db.init_db()
    yield db
    db.set_db_path(old_path)


@pytest.fixture
def app():
    """创建 Flask 测试客户端"""
    import db
    import web

    # 使用临时数据库
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    old_path = db._DB_PATH
    db.set_db_path(db_path)
    db.init_db()

    web.app.config["TESTING"] = True
    web.app.config["WTF_CSRF_ENABLED"] = False
    yield web.app

    db.set_db_path(old_path)
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest.fixture
def client(app):
    """Flask 测试客户端（重置速率限制）"""
    import web_utils
    web_utils._rate_limit_store.clear()
    return app.test_client()


@pytest.fixture
def auth_client(client):
    """已登录的 Flask 测试客户端"""
    import web_utils
    web_utils._rate_limit_store.clear()
    # 注册
    rv = client.post("/api/register", json={"username": "testuser", "password": "testpass123"})
    assert rv.status_code == 200, f"注册失败: {rv.get_json()}"
    # 确认已登录
    rv = client.get("/api/me")
    assert rv.get_json().get("logged_in"), "登录状态异常"
    return client


@pytest.fixture
def mock_llm_client():
    """Mock LLM 客户端，不实际调用 API"""
    from unittest.mock import MagicMock
    from llm_client import LLMClient

    client = MagicMock(spec=LLMClient)
    client.chat.return_value = '{"testcases": []}'
    client.max_retries = 1
    return client


# ============================================================
# 测试用例样本数据
# ============================================================

SAMPLE_TESTCASES = [
    {
        "id": "TC_001",
        "module": "用户登录",
        "title": "正确用户名密码登录",
        "precondition": "用户已注册",
        "steps": "1. 打开登录页\n2. 输入用户名\n3. 输入密码\n4. 点击登录",
        "expected": "登录成功，跳转首页",
        "priority": "P1",
        "type": "功能测试",
    },
    {
        "id": "TC_002",
        "module": "用户登录",
        "title": "密码错误登录失败",
        "precondition": "用户已注册",
        "steps": "1. 打开登录页\n2. 输入正确用户名\n3. 输入错误密码\n4. 点击登录",
        "expected": "提示密码错误",
        "priority": "P1",
        "type": "异常测试",
    },
    {
        "id": "TC_003",
        "module": "用户登录",
        "title": "用户名为空登录",
        "precondition": "进入登录页",
        "steps": "1. 打开登录页\n2. 用户名留空\n3. 输入密码\n4. 点击登录",
        "expected": "提示请输入用户名",
        "priority": "P2",
        "type": "边界测试",
    },
]

SAMPLE_REQUIREMENT = "用户通过手机号+验证码登录系统，手机号为11位，验证码为6位数字。"
