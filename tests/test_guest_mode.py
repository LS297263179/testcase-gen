"""公共实例（免登录访客）+ 模型配置明文 Key 不出接口"""

import json

import pytest


@pytest.fixture
def guest_env(monkeypatch):
    """启用免登录：共享访客自动登录 + 关闭注册 + 锁定配置写入"""
    monkeypatch.setenv("GUEST_AUTO_LOGIN", "1")
    monkeypatch.setenv("GUEST_USERNAME", "shared")
    monkeypatch.setenv("GUEST_LOCK_CONFIG", "1")


def _csrf(client) -> str:
    return client.get("/api/me").get_json()["csrf_token"]


class TestGuestAutoLogin:
    """开关打开后，无需任何登录动作即可直接使用（登录/注册页不再出现）"""

    def test_index_renders_without_login(self, client, guest_env):
        assert client.get("/").status_code == 200

    def test_me_is_logged_in_as_shared_guest(self, client, guest_env):
        data = client.get("/api/me").get_json()
        assert data["logged_in"] is True
        assert data["user"]["username"] == "shared"

    def test_guest_user_created_once_and_reused(self, client, guest_env):
        """共享账号幂等：多次请求只留 1 行，不会每次访问都建号"""
        from core import db

        client.get("/api/me")
        client.get("/api/me")
        with db.db_read_conn() as conn:
            rows = conn.execute("SELECT id FROM users WHERE username = 'shared'").fetchall()
        assert len(rows) == 1

    def test_protected_endpoint_reachable(self, client, guest_env):
        assert client.get("/api/dashboard").status_code == 200

    def test_v2_page_no_longer_redirects(self, client, guest_env):
        assert client.get("/v2").status_code == 200

    def test_register_is_closed(self, client, guest_env):
        rv = client.post("/api/register", json={"username": "stranger", "password": "password123"})
        assert rv.status_code == 403

    def test_health_stays_anonymous(self, client, guest_env):
        """健康检查不该制造 session（容器 healthcheck 每 30s 打一次）"""
        rv = client.get("/api/health")
        assert rv.status_code == 200
        assert "Set-Cookie" not in rv.headers

    def test_mode_off_by_default_keeps_login_required(self, client):
        assert client.get("/api/dashboard").status_code == 401


class TestGuestConfigLock:
    """免登录实例上访客不得改模型配置"""

    def test_post_denied_when_locked(self, client, guest_env):
        rv = client.post(
            "/api/model-config",
            json={"config": {"generate": {"model": "evil"}}},
            headers={"X-CSRF-Token": _csrf(client)},
        )
        assert rv.status_code == 403

    def test_post_allowed_after_admin_login(self, client, guest_env):
        """锁只挡共享访客：正式账号登录进来必须还能改配置，否则把自己也锁死了"""
        from core import db

        db.create_user("ops", "real-password-123")
        client.get("/api/me")  # 先落到访客态
        login = client.post("/api/login", json={"username": "ops", "password": "real-password-123"})
        rv = client.post(
            "/api/model-config",
            json={"config": {"generate": {"api_type": "openai", "model": "m", "base_url": "https://x/v1"}}},
            headers={"X-CSRF-Token": login.get_json()["csrf_token"]},
        )
        assert rv.status_code == 200

    def test_post_allowed_when_guest_unlocked(self, client, monkeypatch):
        monkeypatch.setenv("GUEST_AUTO_LOGIN", "1")
        monkeypatch.delenv("GUEST_LOCK_CONFIG", raising=False)
        rv = client.post(
            "/api/model-config",
            json={"config": {"generate": {"api_type": "openai", "model": "m", "base_url": "https://x/v1"}}},
            headers={"X-CSRF-Token": _csrf(client)},
        )
        assert rv.status_code == 200

    def test_admin_login_still_works_in_guest_mode(self, client, guest_env):
        """正式账号仍能用 /api/login 登进去（钩子只补空 session）"""
        from core import db

        db.create_user("ops", "real-password-123")
        rv = client.post("/api/login", json={"username": "ops", "password": "real-password-123"})
        assert rv.status_code == 200
        assert rv.get_json()["user"]["username"] == "ops"


class TestModelConfigKeyNotExposed:
    """GET /api/model-config 只出掩码，不出明文 Key"""

    _SECRET = "sk-secret-abcdef123456"

    def _save(self):
        from core import db

        db.save_model_config(
            {
                "generate": {
                    "api_type": "openai",
                    "base_url": "https://api.example.com/v1",
                    "api_key": self._SECRET,
                    "model": "some-model",
                },
                "review": {"enabled": False, "api_key": self._SECRET, "model": "review-model"},
            }
        )

    def test_plaintext_key_absent_from_response(self, auth_client):
        self._save()
        rv = auth_client.get("/api/model-config")
        assert rv.status_code == 200
        assert self._SECRET not in json.dumps(rv.get_json(), ensure_ascii=False)

    def test_hint_kept_and_key_dropped(self, auth_client):
        self._save()
        generate = auth_client.get("/api/model-config").get_json()["config"]["generate"]
        assert "api_key" not in generate
        assert generate["api_key_hint"] == "sk-s****3456"

    def test_saving_with_empty_key_keeps_stored_key(self, auth_client):
        """前端把 Key 输入框恒置空：留空保存必须保留原 Key（掩码改造后仍能工作）"""
        from core import db

        self._save()
        token = auth_client.get("/api/me").get_json()["csrf_token"]
        rv = auth_client.post(
            "/api/model-config",
            json={
                "config": {
                    "generate": {
                        "api_type": "openai",
                        "base_url": "https://api.example.com/v1",
                        "api_key": "",
                        "model": "some-model",
                    },
                    "review": {"enabled": False, "api_key": "", "model": "review-model"},
                }
            },
            headers={"X-CSRF-Token": token},
        )
        assert rv.status_code == 200
        cfg = json.loads(db.get_setting("model_config"))
        assert db.decrypt_api_key(cfg["generate"]["api_key"]) == self._SECRET
