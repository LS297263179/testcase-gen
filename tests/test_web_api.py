"""Web API 集成测试"""

import json

import pytest


# ============================================================
# 认证流程
# ============================================================

class TestAuth:

    def test_register_success(self, client):
        rv = client.post("/api/register", json={"username": "alice", "password": "password123"})
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["success"] is True
        assert data["user"]["username"] == "alice"
        assert "csrf_token" in data

    def test_register_short_password(self, client):
        rv = client.post("/api/register", json={"username": "alice", "password": "short"})
        assert rv.status_code == 400

    def test_register_duplicate(self, client):
        client.post("/api/register", json={"username": "alice", "password": "password123"})
        rv = client.post("/api/register", json={"username": "alice", "password": "password123"})
        assert rv.status_code == 409

    def test_login_success(self, client):
        client.post("/api/register", json={"username": "alice", "password": "password123"})
        # 登出后重新登录
        client.post("/api/logout")
        rv = client.post("/api/login", json={"username": "alice", "password": "password123"})
        assert rv.status_code == 200
        assert rv.get_json()["success"] is True

    def test_login_wrong_password(self, client):
        client.post("/api/register", json={"username": "alice", "password": "password123"})
        rv = client.post("/api/login", json={"username": "alice", "password": "wrong"})
        assert rv.status_code == 401

    def test_me_logged_in(self, auth_client):
        rv = auth_client.get("/api/me")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["logged_in"] is True
        assert data["user"]["username"] == "testuser"

    def test_me_not_logged_in(self, client):
        rv = client.get("/api/me")
        assert rv.status_code == 200
        assert rv.get_json()["logged_in"] is False

    def test_logout(self, auth_client):
        rv = auth_client.post("/api/logout")
        assert rv.status_code == 200
        rv = auth_client.get("/api/me")
        assert rv.get_json()["logged_in"] is False


# ============================================================
# CSRF 保护
# ============================================================

class TestCSRF:

    def test_post_without_csrf_token(self, auth_client):
        # 登录状态下不带 CSRF token 的 POST 应被拒
        rv = auth_client.post(
            "/api/materials",
            data={"title": "test"},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 403

    def test_post_with_wrong_csrf_token(self, auth_client):
        rv = auth_client.post(
            "/api/materials",
            data={"title": "test"},
            headers={"X-CSRF-Token": "wrong-token"},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 403


# ============================================================
# 受保护接口
# ============================================================

class TestProtectedEndpoints:

    def test_dashboard_requires_login(self, client):
        rv = client.get("/api/dashboard")
        assert rv.status_code == 401

    def test_materials_requires_login(self, client):
        rv = client.get("/api/materials")
        assert rv.status_code == 401

    def test_history_requires_login(self, client):
        rv = client.get("/api/history")
        assert rv.status_code == 401


# ============================================================
# 健康检查
# ============================================================

class TestHealth:

    def test_health_check(self, client):
        rv = client.get("/api/health")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["status"] == "ok"
        assert data["database"] == "ok"


# ============================================================
# 项目资料 CRUD
# ============================================================

class TestMaterialsAPI:

    def test_create_material(self, auth_client):
        rv = auth_client.post(
            "/api/materials",
            data={"title": "需求文档", "content": "详细内容"},
            headers={"X-CSRF-Token": _get_csrf(auth_client)},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 200
        assert rv.get_json()["success"] is True

    def test_list_materials(self, auth_client):
        # 创建一个资料
        auth_client.post(
            "/api/materials",
            data={"title": "需求文档", "content": "内容"},
            headers={"X-CSRF-Token": _get_csrf(auth_client)},
            content_type="multipart/form-data",
        )
        rv = auth_client.get("/api/materials")
        assert rv.status_code == 200
        mats = rv.get_json()["materials"]
        assert len(mats) >= 1

    def test_delete_material(self, auth_client):
        # 创建
        rv = auth_client.post(
            "/api/materials",
            data={"title": "要删除的", "content": "内容"},
            headers={"X-CSRF-Token": _get_csrf(auth_client)},
            content_type="multipart/form-data",
        )
        mid = rv.get_json()["id"]
        # 删除
        rv = auth_client.delete(
            f"/api/materials/{mid}",
            headers={"X-CSRF-Token": _get_csrf(auth_client)},
        )
        assert rv.status_code == 200
        # 确认已删除
        rv = auth_client.get(f"/api/materials/{mid}")
        assert rv.status_code == 404


# ============================================================
# 历史记录
# ============================================================

class TestHistoryAPI:

    def test_list_history_empty(self, auth_client):
        rv = auth_client.get("/api/history")
        assert rv.status_code == 200
        assert rv.get_json()["sessions"] == []


# ============================================================
# 模型配置
# ============================================================

class TestModelConfigAPI:

    def test_get_model_presets(self, auth_client):
        rv = auth_client.get("/api/model-presets")
        assert rv.status_code == 200
        presets = rv.get_json()["presets"]
        assert "mimo" in presets

    def test_get_model_config(self, auth_client):
        rv = auth_client.get("/api/model-config")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "config" in data
        assert "presets" in data


# ============================================================
# 工具函数
# ============================================================

def _get_csrf(client):
    """从 session 中获取 CSRF token"""
    with client.session_transaction() as sess:
        return sess.get("csrf_token", "")
