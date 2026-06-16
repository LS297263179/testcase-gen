"""db.py 数据库层测试"""

import json
import time

import pytest

import db


# ============================================================
# 用户 CRUD
# ============================================================

class TestUserCRUD:

    def test_create_user(self, tmp_db):
        uid = db.create_user("alice", "password123")
        assert uid > 0

    def test_create_duplicate_user(self, tmp_db):
        db.create_user("alice", "password123")
        with pytest.raises(ValueError, match="已存在"):
            db.create_user("alice", "another_pass")

    def test_verify_user_success(self, tmp_db):
        db.create_user("alice", "password123")
        result = db.verify_user("alice", "password123")
        assert result is not None
        assert result["username"] == "alice"

    def test_verify_user_wrong_password(self, tmp_db):
        db.create_user("alice", "password123")
        result = db.verify_user("alice", "wrong")
        assert result is None

    def test_verify_nonexistent_user(self, tmp_db):
        result = db.verify_user("nobody", "pass")
        assert result is None

    def test_get_user_by_id(self, tmp_db):
        uid = db.create_user("alice", "password123")
        user = db.get_user_by_id(uid)
        assert user["username"] == "alice"

    def test_get_user_by_invalid_id(self, tmp_db):
        user = db.get_user_by_id(99999)
        assert user is None


# ============================================================
# Session CRUD
# ============================================================

class TestSessionCRUD:

    def test_create_and_get_session(self, tmp_db):
        uid = db.create_user("alice", "password123")
        tc = [{"id": "TC_001", "title": "test"}]
        sid = db.create_session(
            requirement="测试需求",
            testcases=tc,
            priority="P1",
            case_types=["功能测试"],
            user_id=uid,
        )
        assert sid > 0

        record = db.get_session(sid)
        assert record is not None
        assert record["requirement"] == "测试需求"
        assert record["testcases"][0]["title"] == "test"
        assert record["tc_count"] == 1

    def test_list_sessions(self, tmp_db):
        uid = db.create_user("alice", "password123")
        for i in range(3):
            db.create_session(
                requirement=f"需求{i}",
                testcases=[{"id": f"TC_{i}"}],
                user_id=uid,
            )
        sessions = db.list_sessions(user_id=uid)
        assert len(sessions) == 3

    def test_soft_delete_session(self, tmp_db):
        uid = db.create_user("alice", "password123")
        sid = db.create_session(
            requirement="测试",
            testcases=[{"id": "TC_001"}],
            user_id=uid,
        )
        db.delete_session(sid)
        record = db.get_session(sid)
        assert record is None

    def test_save_review(self, tmp_db):
        uid = db.create_user("alice", "password123")
        sid = db.create_session(
            requirement="测试",
            testcases=[{"id": "TC_001"}],
            user_id=uid,
        )
        db.save_review(sid, "评审报告内容")
        record = db.get_session(sid)
        assert record["review_report"] == "评审报告内容"


# ============================================================
# Settings
# ============================================================

class TestSettings:

    def test_set_and_get(self, tmp_db):
        db.set_setting("my_key", "my_value")
        assert db.get_setting("my_key") == "my_value"

    def test_get_nonexistent(self, tmp_db):
        assert db.get_setting("no_such_key") is None

    def test_upsert(self, tmp_db):
        db.set_setting("key", "v1")
        db.set_setting("key", "v2")
        assert db.get_setting("key") == "v2"


# ============================================================
# API Key 加密
# ============================================================

class TestApiKeyEncryption:

    def test_encrypt_decrypt_roundtrip(self, tmp_db):
        original = "sk-test-api-key-12345"
        encrypted = db.encrypt_api_key(original)
        assert encrypted != original
        decrypted = db.decrypt_api_key(encrypted)
        assert decrypted == original

    def test_already_encrypted_passthrough(self, tmp_db):
        # gAAAAA 开头的视为已加密，直接返回
        fake_encrypted = "gAAAAAxxxx"
        result = db.encrypt_api_key(fake_encrypted)
        assert result == fake_encrypted

    def test_empty_passthrough(self, tmp_db):
        assert db.encrypt_api_key("") == ""
        assert db.decrypt_api_key("") == ""


# ============================================================
# 偏好规则
# ============================================================

class TestPreferences:

    def test_save_and_list_preferences(self, tmp_db):
        uid = db.create_user("alice", "password123")
        sid = db.create_session(
            requirement="测试",
            testcases=[{"id": "TC_001"}],
            user_id=uid,
        )
        prefs = [
            {"category": "step_style", "pattern": "步骤应使用具体元素名称"},
            {"category": "priority", "pattern": "核心功能标记为P0"},
        ]
        db.save_preferences(prefs, sid, user_id=uid)

        result = db.list_all_preferences(user_id=uid)
        assert len(result) == 2

    def test_preference_weight_decay(self, tmp_db):
        uid = db.create_user("alice", "password123")
        sid = db.create_session(
            requirement="测试",
            testcases=[{"id": "TC_001"}],
            user_id=uid,
        )
        # 保存两轮同 category 偏好
        db.save_preferences([{"category": "step_style", "pattern": "规则1"}], sid, user_id=uid)
        db.save_preferences([{"category": "step_style", "pattern": "规则2"}], sid, user_id=uid)

        prefs = db.get_active_preferences(user_id=uid)
        # 旧规则权重被衰减，新规则权重为 1.0
        weights = {p["pattern"]: p["weight"] for p in prefs}
        assert weights.get("规则2") == 1.0
        if "规则1" in weights:
            assert weights["规则1"] < 1.0

    def test_delete_preference(self, tmp_db):
        uid = db.create_user("alice", "password123")
        sid = db.create_session(
            requirement="测试",
            testcases=[{"id": "TC_001"}],
            user_id=uid,
        )
        db.save_preferences([{"category": "step_style", "pattern": "规则"}], sid, user_id=uid)
        prefs = db.list_all_preferences(user_id=uid)
        pid = prefs[0]["id"]

        db.delete_preference(pid)
        assert len(db.list_all_preferences(user_id=uid)) == 0


# ============================================================
# 项目资料
# ============================================================

class TestMaterials:

    def test_create_and_list(self, tmp_db):
        uid = db.create_user("alice", "password123")
        mid = db.create_material(uid, "需求文档", "内容")
        assert mid > 0

        mats = db.list_materials(uid)
        assert len(mats) == 1
        assert mats[0]["title"] == "需求文档"

    def test_get_material(self, tmp_db):
        uid = db.create_user("alice", "password123")
        mid = db.create_material(uid, "需求文档", "详细内容")
        m = db.get_material(mid)
        assert m["content"] == "详细内容"

    def test_delete_material(self, tmp_db):
        uid = db.create_user("alice", "password123")
        mid = db.create_material(uid, "需求文档", "内容")
        db.delete_material(mid)
        assert db.get_material(mid) is None

    def test_create_with_images(self, tmp_db):
        uid = db.create_user("alice", "password123")
        images = [{"data": "base64data", "media_type": "image/png", "filename": "test.png"}]
        mid = db.create_material(uid, "需求文档", "内容", images=images)
        m = db.get_material(mid)
        assert len(m["images"]) == 1


# ============================================================
# 仪表盘统计
# ============================================================

class TestDashboard:

    def test_empty_dashboard(self, tmp_db):
        uid = db.create_user("alice", "password123")
        stats = db.get_dashboard_stats(uid)
        assert stats["total_sessions"] == 0
        assert stats["total_testcases"] == 0

    def test_dashboard_with_sessions(self, tmp_db):
        uid = db.create_user("alice", "password123")
        for i in range(3):
            db.create_session(
                requirement=f"需求{i}",
                testcases=[{"id": f"TC_{j}"} for j in range(5)],
                user_id=uid,
            )
        stats = db.get_dashboard_stats(uid)
        assert stats["total_sessions"] == 3
        assert stats["total_testcases"] == 15
