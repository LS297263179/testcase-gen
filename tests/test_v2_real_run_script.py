"""Step 10.5 CLI smoke 测试 - `scripts/v2_real_run.py`。

覆盖：
  - argparse 参数组合校验（--requirement / --text 二选一，文件存在性）
  - --dry-run 轻量语义（不读文件 / 不建 user / 不碰 DB / 不调 LLM）
  - config_snapshot 白名单（只挑允许字段，不含 api_key/base_url）
  - assert_no_sensitive 双保险（拦 api_key/authorization/bearer；不误伤 max_tokens）
  - write_output_json 落盘 + 敏感自检
  - _infer_source_type 按扩展名/显式覆盖
  - run_cli mock 掉 run_v2_pipeline：成功退出码 0 + 失败退出码 1 + JSON 落盘
  - resolve_v2_user 自动开通路径（按 username 查/建）

★ 全 mock，不依赖真实 LLM / 真实 data_v2.db。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts.v2_real_run import (
    CONFIG_SNAPSHOT_WHITELIST,
    _infer_source_type,
    _validate_args,
    assert_no_sensitive,
    build_arg_parser,
    build_config_snapshot,
    build_output_payload,
    do_dry_run,
    main,
    resolve_v2_user,
    run_cli,
    write_output_json,
)

# ============================================================
# 1. argparse 与 --help
# ============================================================


def test_01_help_exits_zero(capsys):
    """--help 退出码 0，含关键参数说明。"""
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--requirement" in out
    assert "--dry-run" in out
    assert "--stop-after" in out


def test_02_validate_args_missing_source():
    """--requirement 与 --text 都不给 → SystemExit。"""
    parser = build_arg_parser()
    args = parser.parse_args(["--title", "T"])
    with pytest.raises(SystemExit):
        _validate_args(args)


def test_03_validate_args_both_sources():
    """--requirement 与 --text 都给 → SystemExit（二选一）。"""
    parser = build_arg_parser()
    args = parser.parse_args(["--requirement", "examples/x.md", "--text", "y"])
    with pytest.raises(SystemExit):
        _validate_args(args)


def test_04_validate_args_file_not_exist():
    """--requirement 指向不存在文件 → SystemExit。"""
    parser = build_arg_parser()
    args = parser.parse_args(["--requirement", "/no/such/file.md"])
    with pytest.raises(SystemExit):
        _validate_args(args)


# ============================================================
# 2. --dry-run 轻量语义
# ============================================================


def test_05_dry_run_no_db_no_llm(capsys, monkeypatch, tmp_path):
    """--dry-run：不读文件 / 不建 user / 不碰 DB / 不调 LLM；只打印参数与配置摘要。"""

    # 哨兵：任何触及 DB / LLM 的路径都直接 fail
    def _boom(*a, **kw):
        raise AssertionError("dry-run 不应触及 DB / LLM")

    monkeypatch.setattr("scripts.v2_real_run.ensure_v2_ready", _boom)
    monkeypatch.setattr("scripts.v2_real_run.resolve_v2_user", _boom)
    monkeypatch.setattr("scripts.v2_real_run.run_v2_pipeline", _boom)

    parser = build_arg_parser()
    args = parser.parse_args(["--text", "x", "--dry-run", "--output-dir", str(tmp_path)])
    rc = do_dry_run(args)
    assert rc == 0

    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "config_snapshot" in out
    # dry-run 不落 JSON
    assert list(tmp_path.iterdir()) == []


# ============================================================
# 3. config_snapshot 白名单
# ============================================================


def test_06_config_snapshot_whitelist_only(monkeypatch):
    """build_config_snapshot 只输出白名单字段；api_key/base_url 绝不出现。"""
    fake_cfg = {
        "generate": {
            "api_type": "openai",
            "base_url": "https://secret-gw.example.com/v1",
            "api_key": "sk-SUPER-SECRET-KEY",
            "model": "test-model",
            "temperature": 0.3,
            "max_tokens": 4096,
            "enable_thinking": False,
            "unexpected_field": "should_not_leak",
        },
        "review": {
            "enabled": True,
            "api_type": "openai",
            "base_url": "https://secret-gw.example.com/v1",
            "api_key": "sk-REVIEW-SECRET",
            "model": "review-model",
            "temperature": 0.2,
        },
    }
    monkeypatch.setattr("core.config.get_model_config", lambda: fake_cfg)

    snap = build_config_snapshot()
    assert set(snap.keys()) == {"llm", "prompt_versions"}
    gen = snap["llm"]["generate"]
    rev = snap["llm"]["review"]

    # 白名单字段全在
    for k in CONFIG_SNAPSHOT_WHITELIST["generate"]:
        if k in fake_cfg["generate"]:
            assert k in gen
    for k in CONFIG_SNAPSHOT_WHITELIST["review"]:
        if k in fake_cfg["review"]:
            assert k in rev

    # 敏感字段绝无
    snap_str = json.dumps(snap).lower()
    assert "sk-super-secret-key" not in snap_str
    assert "sk-review-secret" not in snap_str
    assert "secret-gw.example.com" not in snap_str
    assert "api_key" not in snap_str
    assert "base_url" not in snap_str
    assert "unexpected_field" not in snap_str

    # prompt_versions 齐全（6 个）
    assert set(snap["prompt_versions"].keys()) == {
        "parser",
        "tp_generator",
        "tp_completer",
        "tc_synthesizer",
        "tc_pattern_data",
        "reviewer",
    }


# ============================================================
# 4. assert_no_sensitive 双保险
# ============================================================


def test_07_assert_no_sensitive_blocks_api_key():
    """含 api_key/authorization/bearer → RuntimeError。"""
    for bad in ('{"api_key": "sk-xxx"}', '{"Authorization": "Bearer abc"}', '{"access_token": "t"}'):
        with pytest.raises(RuntimeError, match="敏感关键字"):
            assert_no_sensitive(bad)


def test_08_assert_no_sensitive_allows_max_tokens():
    """max_tokens / password_hash / thinking 等非凭证字段不误伤。"""
    ok_payloads = [
        '{"max_tokens": 4096}',
        '{"password_hash": ""}',
        '{"enable_thinking": false}',
        '{"model": "deepseek-v4"}',
    ]
    for p in ok_payloads:
        assert_no_sensitive(p)  # 不抛即通过


# ============================================================
# 5. write_output_json 落盘 + 敏感自检
# ============================================================


def test_09_write_output_json_success(tmp_path):
    """正常 payload 落盘成功，文件名含 run_id，内容可 JSON 解析。"""
    payload = {"run_id": "01ARZ_TEST", "success": True, "steps": []}
    path = write_output_json(payload, tmp_path, "01ARZ_TEST")
    assert path.name == "v2_real_run_01ARZ_TEST.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == "01ARZ_TEST"


def test_10_write_output_json_no_run_id_uses_timestamp(tmp_path):
    """run_id 为 None（stop_after=ir）时，文件名兜底含 timestamp + no_run。"""
    payload = {"run_id": None, "success": True}
    path = write_output_json(payload, tmp_path, None)
    assert "no_run" in path.name
    assert path.exists()


def test_11_write_output_json_blocks_sensitive(tmp_path):
    """payload 里混入 api_key → 写盘前 assert 抛 RuntimeError，文件不落盘。"""
    payload = {"run_id": "X", "api_key": "sk-LEAKED"}
    with pytest.raises(RuntimeError, match="敏感关键字"):
        write_output_json(payload, tmp_path, "X")
    assert list(tmp_path.iterdir()) == []  # 未落盘


# ============================================================
# 6. _infer_source_type
# ============================================================


def test_12_infer_source_type_by_extension():
    """按 --requirement 扩展名推断；显式 --source-type 覆盖。"""
    parser = build_arg_parser()

    args = parser.parse_args(["--requirement", "examples/x.md"])
    assert _infer_source_type(args).value == "markdown"

    args = parser.parse_args(["--requirement", "examples/x.txt"])
    assert _infer_source_type(args).value == "text"

    args = parser.parse_args(["--requirement", "examples/x.xlsx"])
    assert _infer_source_type(args).value == "excel"

    args = parser.parse_args(["--text", "inline"])
    assert _infer_source_type(args).value == "text"

    # 显式覆盖
    args = parser.parse_args(["--requirement", "examples/x.md", "--source-type", "text"])
    assert _infer_source_type(args).value == "text"


# ============================================================
# 7. resolve_v2_user 自动查/建
# ============================================================


def test_13_resolve_v2_user_autocreates(v2_db, monkeypatch):
    """--username 查不到时自动开通 V2 user（legacy_int_id=NULL）。"""
    # 屏蔽日志噪声
    monkeypatch.setattr("scripts.v2_real_run.logger", MagicMock())
    uid = resolve_v2_user(username="cli_real_run", user_id=None)
    assert uid and len(uid) == 26  # ULID
    # 再查一次，复用同一 uid（不重复建）
    uid2 = resolve_v2_user(username="cli_real_run", user_id=None)
    assert uid2 == uid


def test_14_resolve_v2_user_explicit_id_must_exist(v2_db):
    """--user-id 显式给但不存在 → SystemExit。"""
    with pytest.raises(SystemExit, match="不存在"):
        resolve_v2_user(username=None, user_id="01ARZ_NOT_EXIST_XXXXXXXXXXXX")


# ============================================================
# 8. build_output_payload 结构
# ============================================================


def test_15_build_output_payload_structure(v2_db, monkeypatch):
    """PipelineResult → payload 含关键字段 + config_snapshot 白名单。"""
    monkeypatch.setattr(
        "core.config.get_model_config",
        lambda: {
            "generate": {"api_type": "openai", "model": "m", "temperature": 0.3, "api_key": "sk-X"},
            "review": {"enabled": False},
        },
    )
    fake_result = SimpleNamespace(
        run_id=None,
        success=True,
        failed_step=None,
        error_message=None,
        steps=[],
        total_duration_ms=100,
        total_llm_calls=0,
        doc_id="01DOC",
        version_id="01VER",
        test_point_count=0,
        test_case_count=0,
        review_report_id=None,
        optimizer_result=None,
    )
    payload = build_output_payload(fake_result, None)
    assert payload["success"] is True
    assert payload["doc_id"] == "01DOC"
    assert "config_snapshot" in payload
    assert "api_key" not in json.dumps(payload).lower()


# ============================================================
# 9. run_cli 端到端（mock run_v2_pipeline）
# ============================================================


def _install_pipeline_mock(monkeypatch, *, success=True, run_id="01ARZ_MOCK_RUN", failed_step=None):
    """把 run_v2_pipeline 替换为返回受控 PipelineResult 的 mock（不真调 LLM）。"""
    fake_result = SimpleNamespace(
        run_id=run_id,
        success=success,
        failed_step=failed_step,
        error_message=None if success else "mocked failure",
        steps=[
            SimpleNamespace(
                step="ir",
                success=True,
                started_at=None,
                finished_at=None,
                duration_ms=10,
                llm_calls=0,
                error=None,
                artifact_ids={"doc_id": "01DOC"},
                counts={"items": 3},
            )
        ],
        total_duration_ms=100,
        total_llm_calls=5,
        doc_id="01DOC",
        version_id="01VER",
        test_point_count=8,
        test_case_count=8,
        review_report_id=None,
        optimizer_result=None,
    )
    monkeypatch.setattr("scripts.v2_real_run.run_v2_pipeline", lambda **kw: fake_result)
    monkeypatch.setattr("scripts.v2_real_run.ensure_v2_ready", lambda: None)
    monkeypatch.setattr("scripts.v2_real_run.resolve_v2_user", lambda u, i: "01ARZ_USER_MOCK")
    # repo.get_run 兜底：mock 返回 None，避免依赖真实 DB 行
    monkeypatch.setattr("scripts.v2_real_run.repo.get_run", lambda rid: None)
    return fake_result


def test_16_run_cli_success_exits_zero(monkeypatch, tmp_path, capsys):
    """mock 成功 PipelineResult → 退出码 0 + JSON 落盘 + 控制台汇总。"""
    _install_pipeline_mock(monkeypatch, success=True, run_id="01ARZ_OK")
    monkeypatch.setattr(
        "core.config.get_model_config",
        lambda: {
            "generate": {
                "api_type": "openai",
                "model": "m",
                "temperature": 0.3,
                "max_tokens": 4096,
                "enable_thinking": False,
                "api_key": "sk-X",
            },
            "review": {"enabled": False},
        },
    )
    parser = build_arg_parser()
    args = parser.parse_args(["--text", "x", "--output-dir", str(tmp_path)])
    rc = run_cli(args)
    assert rc == 0

    # JSON 落盘
    files = list(tmp_path.glob("v2_real_run_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["run_id"] == "01ARZ_OK"
    assert data["success"] is True
    assert "api_key" not in json.dumps(data).lower()

    # 控制台汇总
    out = capsys.readouterr().out
    assert "V2 真实运行汇总" in out
    assert "01ARZ_OK" in out


def test_17_run_cli_failure_exits_one(monkeypatch, tmp_path):
    """mock 失败 PipelineResult → 退出码 1 + JSON 仍落盘（含 failed_step）。"""
    _install_pipeline_mock(monkeypatch, success=False, run_id="01ARZ_FAIL", failed_step="testcases")
    monkeypatch.setattr(
        "core.config.get_model_config",
        lambda: {
            "generate": {"api_type": "openai", "model": "m", "temperature": 0.3, "api_key": "sk-X"},
            "review": {"enabled": False},
        },
    )
    parser = build_arg_parser()
    args = parser.parse_args(["--text", "x", "--output-dir", str(tmp_path)])
    rc = run_cli(args)
    assert rc == 1

    files = list(tmp_path.glob("v2_real_run_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["success"] is False
    assert data["failed_step"] == "testcases"


def test_18_main_dry_run_end_to_end(capsys, monkeypatch, tmp_path):
    """main(argv) 走 --dry-run 全路径：退出码 0，不触发 run_cli。"""
    called = {"run_cli": False}

    def _fake_run_cli(args):
        called["run_cli"] = True
        return 0

    monkeypatch.setattr("scripts.v2_real_run.run_cli", _fake_run_cli)
    rc = main(["--text", "x", "--dry-run", "--output-dir", str(tmp_path)])
    assert rc == 0
    assert called["run_cli"] is False  # dry-run 短路，不进 run_cli


# ============================================================
# 10. Bootstrap 失败退出码 3
# ============================================================


def test_19_bootstrap_failure_exits_three(monkeypatch, tmp_path):
    """ensure_v2_ready 抛 V2BootstrapError → run_cli 返回 3。"""
    from core.v2.bootstrap import V2BootstrapError

    def _boom():
        raise V2BootstrapError("mocked bootstrap fail")

    monkeypatch.setattr("scripts.v2_real_run.ensure_v2_ready", _boom)
    parser = build_arg_parser()
    args = parser.parse_args(["--text", "x", "--output-dir", str(tmp_path)])
    rc = run_cli(args)
    assert rc == 3


# ============================================================
# 11. sys.path 兜底（scripts/ 作为独立入口）
# ============================================================


def test_20_project_root_on_syspath():
    """CLI 顶部把项目根加入 sys.path，允许 `python scripts/v2_real_run.py` 直接运行。"""
    project_root = Path(__file__).resolve().parent.parent
    assert str(project_root) in sys.path
