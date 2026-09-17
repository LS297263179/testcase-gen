"""Step 10.1 验收测试 - V2 DB 真初始化（bootstrap）。

覆盖计划 14 例：全新初始化 / 幂等 / 空文件仍初始化(补充B) / verify 检测缺表·版本·索引(补充A/C) /
失败即 raise 契约 / 表数辅助断言(补充C) / V1 零影响 / web 隔离契约 / 健康库 verify 空 /
日志双分支 / 已有 v9+业务数据幂等保护(补充①) / foreign_keys 连接级差异(补充②)。

约定：用 tmp_path + set_v2_db_path 切到临时 V2 库并测后恢复；不使用 conftest 的 v2_db fixture
（它会预建 schema，掩盖 ensure_v2_ready 的建表行为）。
"""

import logging
import os
import sqlite3

import pytest
from flask import Flask

from core.schemas import (
    GenerationConfig,
    GenerationScope,
    Priority,
    Provenance,
    RequirementDoc,
    RequirementItem,
    RequirementItemType,
    RequirementVersion,
    Run,
    RunStatus,
    SourceType,
    TestCase,
    TestCaseStatus,
    TestCaseType,
    TestDimension,
    TestPoint,
    TestStep,
)
from core.v2 import bootstrap
from core.v2 import repository as repo
from core.v2.bootstrap import (
    CRITICAL_INDEXES,
    CRITICAL_TABLES,
    V2BootstrapError,
    ensure_v2_ready,
    verify_v2_schema,
)
from core.v2.db import v2_conn, v2_read_conn
from core.v2.ddl import SCHEMA_VERSION, get_schema_version

USER_ID = "01ARZ3NDEKTSV4RRFFQ69G5B01"
ITEM_ID = "01ARZ3NDEKTSV4RRFFQ69G5B02"
TP_ID = "01ARZ3NDEKTSV4RRFFQ69G5B03"
TC_ID = "01ARZ3NDEKTSV4RRFFQ69G5B04"

# 业务表快照范围（幂等保护断言用）
_BUSINESS_TABLES = [
    "users",
    "requirement_docs",
    "requirement_versions",
    "requirement_items",
    "generation_configs",
    "runs",
    "test_points",
    "test_cases",
    "test_point_items",
    "test_case_points",
]


@pytest.fixture
def v2_tmp_db(tmp_path):
    """临时 V2 库路径（文件初始不存在），测后恢复原路径。"""
    from core.v2 import db as v2db

    old = v2db.get_v2_db_path()
    path = str(tmp_path / "bootstrap_v2.db")
    v2db.set_v2_db_path(path)
    yield path
    v2db.set_v2_db_path(old)


def _read_master(kind: str) -> set[str]:
    """读取 sqlite_master 中指定类型（table/index）的名字集合（走 V2 标准连接）。"""
    with v2_read_conn() as conn:
        return {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))}


def _seed_business_data() -> dict:
    """写入一条完整业务链：User→Doc→Version→Item→Config→Run→TestPoint→TestCase（含链接表）。"""
    repo.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    doc = RequirementDoc(user_id=USER_ID, title="Bootstrap 幂等保护", source_type=SourceType.TEXT)
    repo.save_doc(doc)
    ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="需求", provenance=Provenance.LLM)
    repo.save_version(ver)
    item = RequirementItem(
        id=ITEM_ID, version_id=ver.id, seq=1, type=RequirementItemType.FUNCTION, module="登录", statement="手机号登录"
    )
    repo.save_item(item)
    doc.latest_version_id = ver.id
    repo.save_doc(doc)
    cfg = GenerationConfig(
        model_provider="test", model_name="test-model", temperature=0.7, prompt_version="v1", generator_version="v1"
    )
    repo.save_generation_config(cfg)
    run = Run(
        user_id=USER_ID,
        doc_id=doc.id,
        requirement_version_id=ver.id,
        generation_config_id=cfg.id,
        status=RunStatus.DONE,
    )
    repo.save_run(run)
    tp = TestPoint(
        id=TP_ID,
        run_id=run.id,
        version_id=ver.id,
        item_ids=[ITEM_ID],
        module="登录",
        subcategory="校验",
        title="TP-原始",
        description="d",
        dimension=TestDimension.FUNCTIONAL,
        priority=Priority.P1,
        provenance=Provenance.LLM,
        generation_scope=GenerationScope.ITEM,
    )
    repo.save_test_point(tp)
    tc = TestCase(
        id=TC_ID,
        run_id=run.id,
        display_id="TC_001",
        module="登录",
        title="原标题",
        precondition="前置条件",
        steps=[TestStep(seq=1, action="步骤1", expected="ok")],
        expected="预期结果",
        priority=Priority.P1,
        type=TestCaseType.FUNCTIONAL,
        provenance=Provenance.LLM,
        status=TestCaseStatus.REVIEWED,
        test_point_ids=[TP_ID],
        fingerprint="tc_boot_001",
        content_hash="tch_boot_001",
    )
    repo.save_test_case(tc)
    return {"run_id": run.id, "tp_id": TP_ID, "tc_id": TC_ID}


def _business_snapshot() -> dict:
    """业务数据快照：各表行数 + TestCase/TestPoint 关键字段（title/fingerprint/content_hash）。"""
    snap: dict = {}
    with v2_read_conn() as conn:
        for t in _BUSINESS_TABLES:
            snap[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        tc = conn.execute("SELECT title, fingerprint, content_hash FROM test_cases WHERE id = ?", (TC_ID,)).fetchone()
        snap["tc_fields"] = (tc["title"], tc["fingerprint"], tc["content_hash"]) if tc else None
        tp = conn.execute("SELECT title, fingerprint FROM test_points WHERE id = ?", (TP_ID,)).fetchone()
        snap["tp_fields"] = (tp["title"], tp["fingerprint"]) if tp else None
    return snap


# ============================================================
# 1. 全新初始化
# ============================================================


def test_01_fresh_init(v2_tmp_db):
    """全新库：ensure_v2_ready() 不抛，schema_version=10，关键表/索引齐，foreign_keys=ON。"""
    ensure_v2_ready()
    assert SCHEMA_VERSION == 10
    assert get_schema_version() == SCHEMA_VERSION
    assert verify_v2_schema() == []
    assert _read_master("table") >= CRITICAL_TABLES
    assert _read_master("index") >= CRITICAL_INDEXES
    with v2_read_conn() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# ============================================================
# 2. 幂等
# ============================================================


def test_02_idempotent(v2_tmp_db):
    """连调两次不抛，schema_version 与表集合稳定。"""
    ensure_v2_ready()
    tables_first = _read_master("table")
    ensure_v2_ready()
    tables_second = _read_master("table")
    assert get_schema_version() == SCHEMA_VERSION
    assert tables_first == tables_second


# ============================================================
# 3. 补充 B：文件存在但空表 -> 仍完整初始化（复现真实 data_v2.db 现状）
# ============================================================


def test_03_existing_empty_file_still_initialized(v2_tmp_db):
    """先造一个 0 表空 sqlite 文件，ensure_v2_ready() 不因文件存在而跳过，仍完整建表。"""
    raw = sqlite3.connect(v2_tmp_db)
    raw.execute("CREATE TABLE _probe (x INTEGER)")
    raw.execute("DROP TABLE _probe")
    raw.commit()
    raw.close()

    assert os.path.exists(v2_tmp_db)
    assert _read_master("table") == set()  # 空库：0 表

    ensure_v2_ready()  # 不应被"文件已存在"骗过
    assert get_schema_version() == SCHEMA_VERSION
    assert _read_master("table") >= CRITICAL_TABLES


# ============================================================
# 4. 补充 A：verify 检测缺关键表
# ============================================================


def test_04_verify_detects_missing_table(v2_tmp_db):
    """初始化后 DROP 关键表，verify_v2_schema() 按名报缺表。"""
    ensure_v2_ready()
    with v2_conn() as conn:
        conn.execute("DROP TABLE test_cases")
    problems = verify_v2_schema()
    assert any("test_cases" in p for p in problems)


# ============================================================
# 5. 补充 A：verify 检测 schema_version 不符
# ============================================================


def test_05_verify_detects_wrong_schema_version(v2_tmp_db):
    """手改 schema_meta 为错误值，verify_v2_schema() 报版本不符。"""
    ensure_v2_ready()
    with v2_conn() as conn:
        conn.execute("UPDATE schema_meta SET value = '5' WHERE key = 'schema_version'")
    problems = verify_v2_schema()
    assert any("schema_version" in p for p in problems)


# ============================================================
# 6. 补充 C：verify 检测缺关键 UNIQUE 索引
# ============================================================


def test_06_verify_detects_missing_index(v2_tmp_db):
    """DROP 关键 UNIQUE 索引，verify_v2_schema() 报缺索引。"""
    ensure_v2_ready()
    with v2_conn() as conn:
        conn.execute("DROP INDEX ux_test_cases_fingerprint")
    problems = verify_v2_schema()
    assert any("ux_test_cases_fingerprint" in p for p in problems)


# ============================================================
# 7. 契约：create 不足（部分成功却没抛）时 ensure_v2_ready() 必须 raise
# ============================================================


def test_07_ensure_raises_when_schema_incomplete(v2_tmp_db, monkeypatch):
    """模拟 create_v2_schema '部分成功却没抛异常'：patch 成 no-op，全新库校验必失败并 raise。"""
    monkeypatch.setattr(bootstrap, "create_v2_schema", lambda: None)
    with pytest.raises(V2BootstrapError):
        ensure_v2_ready()


# ============================================================
# 8. 补充 C：表总数 >= 21 仅作辅助 smoke 断言（非唯一正确性证明）
# ============================================================


def test_08_table_count_auxiliary(v2_tmp_db):
    """表总数 >= 21 作为辅助断言；用 >= 保证将来新增内部表不会把正常版本误判为失败。"""
    ensure_v2_ready()
    with v2_read_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
    assert count >= 21


# ============================================================
# 9. V1 零影响
# ============================================================


def test_09_v1_db_untouched(v2_tmp_db, tmp_path):
    """ensure_v2_ready() 只动临时 V2 库，绝不创建/修改 V1 data.db。"""
    from core import db as v1db

    v1_tmp = str(tmp_path / "v1_should_not_exist.db")
    old_v1 = v1db._DB_PATH
    v1db.set_db_path(v1_tmp)
    try:
        ensure_v2_ready()
        assert not os.path.exists(v1_tmp)  # V2 引导绝不触碰 V1 库文件
        assert os.path.exists(v2_tmp_db)  # 只初始化了 V2 临时库
    finally:
        v1db.set_db_path(old_v1)


# ============================================================
# 10. web 隔离契约（bootstrap raise，web try/except 隔离，保护 V1）
# ============================================================


def test_10_web_isolates_v2_failure(monkeypatch):
    """_bootstrap_v2：ensure 抛异常时返回 False 且不上抛；正常时返回 True；两分支都写 config。"""
    import web

    def _boom():
        raise RuntimeError("V2 init failed")

    monkeypatch.setattr(web, "ensure_v2_ready", _boom)
    app_failed = Flask(__name__)
    assert web._bootstrap_v2(app_failed) is False
    assert app_failed.config["V2_READY"] is False

    monkeypatch.setattr(web, "ensure_v2_ready", lambda: None)
    app_ok = Flask(__name__)
    assert web._bootstrap_v2(app_ok) is True
    assert app_ok.config["V2_READY"] is True


# ============================================================
# 11. 健康库 verify 返回空 list
# ============================================================


def test_11_verify_healthy_returns_empty(v2_tmp_db):
    """健康库上 verify_v2_schema() 返回空 list（无任何问题）。"""
    ensure_v2_ready()
    assert verify_v2_schema() == []


# ============================================================
# 12. 日志双分支（首次 vs 已就绪）均不抛
# ============================================================


def test_12_log_branches(v2_tmp_db, caplog):
    """before=None 走'首次初始化'分支，before=9 走'已就绪'分支，两条路径均不抛。"""
    with caplog.at_level(logging.INFO):
        ensure_v2_ready()
    assert any("首次初始化" in r.getMessage() for r in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        ensure_v2_ready()
    assert any("已就绪" in r.getMessage() for r in caplog.records)


# ============================================================
# 13. 补充①：已有 v9 + 已有业务数据的幂等保护（不删/不重建/不改）
# ============================================================


def test_13_existing_v9_with_business_data_preserved(v2_tmp_db):
    """已有 v9 + 真实业务数据时再次 ensure_v2_ready()：不抛、版本仍 9、业务行原样保留。"""
    ensure_v2_ready()  # 到 v9
    _seed_business_data()  # 写入真实业务数据

    snap_before = _business_snapshot()
    assert snap_before["test_cases"] >= 1  # 确认确有业务数据

    ensure_v2_ready()  # 二次调用：必须非破坏性

    assert get_schema_version() == SCHEMA_VERSION
    snap_after = _business_snapshot()
    assert snap_after == snap_before  # 行数 + title/fingerprint/content_hash 全部未变


# ============================================================
# 14. 补充②：foreign_keys 连接级差异（verify 必须走 V2 标准连接）
# ============================================================


def test_14_foreign_keys_connection_level_difference(v2_tmp_db):
    """V2 标准连接 foreign_keys=ON，裸连接默认 OFF；verify 不误报，证明其走了 v2_read_conn。"""
    ensure_v2_ready()

    with v2_read_conn() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    raw = sqlite3.connect(v2_tmp_db)  # 裸连接：默认 foreign_keys=OFF
    try:
        assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    finally:
        raw.close()

    # verify 若误用裸连接会报 foreign_keys 问题；此处不报，间接证明走了 V2 标准连接工厂
    assert not any("foreign_keys" in p for p in verify_v2_schema())
