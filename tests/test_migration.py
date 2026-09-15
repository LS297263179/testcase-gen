"""V1 → V2 迁移测试。

对应 docs/v2/step1-data-model.md §9 + §13 验收标准第 5 条。
重点验证 §9.1 铁律：provenance=migrated、不猜历史关系（item_ids 空、run_id NULL）、计数一致。
"""

import pytest

from core.schemas import Provenance, RunStatus, TestCaseStatus, TestCaseType
from core.v2 import repository as repo

V1_CASES = [
    {
        "id": "TC_001",
        "module": "登录",
        "title": "验证码4:59可用",
        "precondition": "已获取验证码",
        "steps": "1. 等待4分59秒\n2. 提交验证码",
        "expected": "验证通过",
        "priority": "P1",
        "type": "边界测试",
    },
    {
        "id": "TC_002",
        "module": "登录",
        "title": "验证码5:01失效",
        "precondition": "",
        "steps": "1. 等待5分01秒\n2. 提交",
        "expected": "提示已过期",
        "priority": "P2",
        "type": "异常测试",
    },
]

V1_POINTS = [
    {
        "module": "登录",
        "subcategories": [
            {"name": "边界条件", "points": [{"title": "有效期边界", "description": "4:59/5:00/5:01"}]},
        ],
    }
]


@pytest.fixture
def migrated(tmp_db, tmp_path):
    """在 V1 临时库造数据 → 迁移到 V2 临时库，返回 (stats, v1_db)"""
    from core.v2 import db as v2db
    from core.v2.migrate_v1_to_v2 import migrate

    db = tmp_db  # V1 db 模块（已 init_db，路径为临时）
    v1_path = db._DB_PATH

    uid = db.create_user("alice", "pass123456")
    sid = db.create_session(requirement="验证码5分钟有效", testcases=V1_CASES, user_id=uid)
    # 补一条评审自由文本（V1 存于 sessions.review_report）
    with db.db_conn() as conn:
        conn.execute("UPDATE sessions SET review_report = ? WHERE id = ?", ("覆盖尚可，建议补充边界。", sid))
    db.save_test_points(uid, "登录测试点", "验证码5分钟有效", V1_POINTS, 1)
    db.save_preferences([{"category": "step_style", "pattern": "步骤用动词开头"}], sid, user_id=uid)

    v2_path = str(tmp_path / "v2.db")
    old = v2db.get_v2_db_path()
    v2db.set_v2_db_path(v2_path)
    try:
        stats = migrate(v1_path)
        yield stats, db
    finally:
        v2db.set_v2_db_path(old)


class TestMigrationCounts:
    def test_counts_consistent(self, migrated):
        stats, _ = migrated
        assert stats["users"] == 1
        assert stats["runs"] == 1
        assert stats["test_cases"] == 2  # 与 V1 tc 数一致
        assert stats["test_points"] == 1  # V1_POINTS 展平后 1 条
        assert stats["preferences"] == 1
        assert stats["review_reports"] == 1

    def test_docs_and_versions(self, migrated):
        stats, _ = migrated
        # 1 个 session + 1 个 test_points 记录 → 2 个 doc/version
        assert stats["docs"] == 2
        assert stats["versions"] == 2


def _first_run():
    """取迁移产生的第一个 Run（多个测试类共用）"""
    from core.v2.db import v2_read_conn

    with v2_read_conn() as conn:
        row = conn.execute("SELECT id FROM runs LIMIT 1").fetchone()
    return repo.get_run(row["id"])


class TestMigrationFidelity:
    def test_run_provenance_and_legacy(self, migrated):
        run = _first_run()
        assert run.legacy_session_id is not None  # 记录原 V1 session id
        assert run.status == RunStatus.DONE
        assert run.requirement_version_id is not None  # P0-2 绑定版本

    def test_cases_migrated_provenance(self, migrated):
        run = _first_run()
        cases = repo.list_test_cases(run.id)
        assert len(cases) == 2
        for c in cases:
            assert c.provenance == Provenance.MIGRATED
            assert c.status == TestCaseStatus.GENERATED

    def test_type_and_priority_mapping(self, migrated):
        run = _first_run()
        cases = {c.display_id: c for c in repo.list_test_cases(run.id)}
        assert cases["TC_001"].type == TestCaseType.BOUNDARY  # 边界测试 → boundary
        assert cases["TC_002"].type == TestCaseType.EXCEPTION  # 异常测试 → exception
        assert cases["TC_002"].priority.value == "P2"

    def test_steps_structured(self, migrated):
        run = _first_run()
        tc1 = next(c for c in repo.list_test_cases(run.id) if c.display_id == "TC_001")
        assert len(tc1.steps) == 2
        assert tc1.steps[0].action == "等待4分59秒"  # 去掉了 "1. " 前缀

    def test_version_raw_text_preserved(self, migrated):
        run = _first_run()
        from core.v2 import repository as r

        ver = r.get_version(run.requirement_version_id)
        assert ver.raw_text == "验证码5分钟有效"
        assert ver.provenance == Provenance.MIGRATED


class TestNoFabricatedTraceability:
    """§9.1 铁律：迁移不得臆造历史关联"""

    def test_test_points_have_no_items_no_run(self, migrated):
        from core.v2.db import v2_read_conn

        with v2_read_conn() as conn:
            rows = conn.execute("SELECT id FROM test_points").fetchall()
        assert len(rows) == 1
        tp = repo.get_test_point(rows[0]["id"])
        assert tp.item_ids == []  # V1 无 IR，不猜需求项关联
        assert tp.run_id is None  # V1 test_points 与 session 分离，不猜 run
        assert tp.version_id is not None  # 但可确定地挂到迁移生成的 version
        assert tp.provenance == Provenance.MIGRATED

    def test_cases_have_no_test_point_links(self, migrated):
        """V1 用例无测试点关联 → 迁移后 test_point_ids 为空（不猜）"""
        run = _first_run()
        for c in repo.list_test_cases(run.id):
            assert c.test_point_ids == []

    def test_review_report_scores_zeroed(self, migrated):
        """V1 自由文本评审 → summary 保留，结构化分数置 0（不编造评分）"""
        run = _first_run()
        reports = repo.list_review_reports(run.id)
        assert len(reports) == 1
        assert "覆盖尚可" in reports[0].summary
        assert reports[0].scores.coverage == 0
        assert reports[0].overall_score == 0.0


class TestOrphanRecords:
    """回归锁定：V1 中 user_id 为 NULL/悬空的孤儿记录必须迁入 legacy 用户，绝不丢弃。

    （真实 data.db 中曾有 2 个 session + 1 个 preference 的 user_id 无法映射）
    """

    def test_orphan_session_migrated_to_legacy_user(self, tmp_db, tmp_path):
        import json

        from core.v2 import db as v2db
        from core.v2.migrate_v1_to_v2 import migrate

        db = tmp_db
        v1_path = db._DB_PATH
        # 直接插一条 user_id=NULL 的孤儿 session
        orphan_cases = [
            {
                "id": "TC_001",
                "module": "m",
                "title": "t",
                "steps": "1. x",
                "expected": "e",
                "priority": "P1",
                "type": "功能测试",
            }
        ]
        with db.db_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (requirement, testcases, tc_count, user_id) VALUES (?,?,?,NULL)",
                ("孤儿需求", json.dumps(orphan_cases, ensure_ascii=False), 1),
            )

        v2_path = str(tmp_path / "orphan_v2.db")
        old = v2db.get_v2_db_path()
        v2db.set_v2_db_path(v2_path)
        try:
            stats = migrate(v1_path)
        finally:
            v2db.set_v2_db_path(old)

        assert stats["runs"] == 1  # 孤儿 session 也迁了，未跳过
        assert stats["test_cases"] == 1  # 用例未丢
        assert stats["legacy_users"] == 1  # 归入合成 legacy 用户
        assert stats["orphan_records"] >= 1
        assert stats["users"] == 1  # V1 无真实用户，仅 legacy
