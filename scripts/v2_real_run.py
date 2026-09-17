"""V2 真实 LLM 全链路 CLI（Step 10.5）—— 权威真实运行验证路径。

用真实 LLM（不 mock）跑通 Step 2 IR → Step 3 TestPoints → Step 4 Strategy
→ Step 5 TestCases → Step 7 Review → Step 8 Optimizer 全链路，产出：
  - `data/data_v2.db` 中的真实 Run/TestPoint/TestCase/ReviewReport 记录
  - `output/v2_real_run_<run_id>.json` 完整运行快照（供 Step 10.7 记录使用）
  - 控制台人读汇总（各阶段耗时 / 计数 / 6 维评分 / 归档数）

★ 安全护栏（PROGRESS §9.3-5）：脚本、日志、output JSON 绝不出现 api_key / Authorization。
  config_snapshot 采用**白名单挑字段**（非黑名单脱敏），从设计上根绝泄漏；
  写盘前再做一次 `assert "api_key" not in json.lower()` 双保险。

★ 边界（PROGRESS §9.8）：本脚本是 CLI **消费层**，不改 Runtime / orchestrator / repository。
  `--stop-after` 直接透传 `run_v2_pipeline`（10.2 已支持）。
  `--dry-run` 轻量：只做 argparse 校验 + 打印脱敏配置摘要，不读文件 / 不建 user / 不碰 DB / 不调 LLM。

用法示例：
  # 完整真实运行（默认 --username cli_real_run 自动查/建 V2 user）
  python scripts/v2_real_run.py --requirement examples/v2_sample_requirement.md \\
      --title "订单退款 V2 真实运行"

  # 分阶段调试（跑到 IR 就停）
  python scripts/v2_real_run.py --text "用户注册需求..." --stop-after ir

  # 只校验参数与配置摘要（不调 LLM）
  python scripts/v2_real_run.py --requirement examples/v2_sample_requirement.md --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

# 允许 `python scripts/v2_real_run.py` 直接运行：把项目根目录加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.schemas import SourceType, new_ulid  # noqa: E402
from core.v2 import repository as repo  # noqa: E402
from core.v2.bootstrap import V2BootstrapError, ensure_v2_ready  # noqa: E402
from core.v2.db import v2_read_conn  # noqa: E402
from core.v2.prompts import PARSER_PROMPT_VERSION  # noqa: E402
from core.v2.review_prompts import REVIEWER_PROMPT_VERSION  # noqa: E402
from core.v2.runtime import STEP_ORDER, run_v2_pipeline  # noqa: E402
from core.v2.tc_prompts import PATTERN_DATA_PROMPT_VERSION, SYNTHESIZER_PROMPT_VERSION  # noqa: E402
from core.v2.tp_prompts import COMPLETER_PROMPT_VERSION, GENERATOR_PROMPT_VERSION  # noqa: E402

logger = logging.getLogger("v2.real_run")

# ============================================================
# 白名单：只允许出现在 config_snapshot / output JSON 里的字段
# ============================================================

CONFIG_SNAPSHOT_WHITELIST: dict[str, tuple[str, ...]] = {
    "generate": ("api_type", "model", "temperature", "max_tokens", "enable_thinking"),
    "review": ("enabled", "api_type", "model", "temperature"),
}

# 6 个 prompt 版本号（供审计"这批产物是哪些 prompt 版本产的"）
PROMPT_VERSIONS: dict[str, str] = {
    "parser": PARSER_PROMPT_VERSION,
    "tp_generator": GENERATOR_PROMPT_VERSION,
    "tp_completer": COMPLETER_PROMPT_VERSION,
    "tc_synthesizer": SYNTHESIZER_PROMPT_VERSION,
    "tc_pattern_data": PATTERN_DATA_PROMPT_VERSION,
    "reviewer": REVIEWER_PROMPT_VERSION,
}

# 敏感关键字（写盘前 assert 用；小写比对）
# ★ 不用宽泛的 "token" / "password"：会误伤 max_tokens / password_hash 等非敏感字段。
#   只拦真正的凭证类关键字（API Key / Authorization header / OAuth token / Bearer）。
_SENSITIVE_KEYS = ("api_key", "apikey", "authorization", "secret_key", "access_token", "auth_token", "bearer")


# ============================================================
# 配置摘要（白名单挑字段，从设计上根绝泄漏）
# ============================================================


def build_config_snapshot() -> dict:
    """从 `core.config.get_model_config()` 返回的 dict 里**只挑白名单字段**，构造可安全落盘的快照。

    ★ 明确不入快照：api_key / base_url（可能带租户 token）/ secret_key / 任何未在 whitelist 的字段。
    """
    from core.config import get_model_config

    cfg = get_model_config() or {}
    snapshot: dict[str, dict] = {}
    for section, allowed_keys in CONFIG_SNAPSHOT_WHITELIST.items():
        raw = cfg.get(section) or {}
        snapshot[section] = {k: raw.get(k) for k in allowed_keys if k in raw}
    return {"llm": snapshot, "prompt_versions": PROMPT_VERSIONS}


def assert_no_sensitive(payload_str: str) -> None:
    """写盘前自检：JSON 字符串（小写）里绝不出现 api_key / authorization 等敏感关键字。

    触发即抛 RuntimeError，阻止泄漏文件落盘（双保险）。
    """
    lowered = payload_str.lower()
    hits = [k for k in _SENSITIVE_KEYS if k in lowered]
    if hits:
        raise RuntimeError(f"output JSON 自检失败：命中敏感关键字 {hits}，拒绝写盘")


# ============================================================
# V2 user 解析（按 username 自动查/建；不改 repository）
# ============================================================


def resolve_v2_user(username: str | None, user_id: str | None) -> str:
    """解析 V2 user id：
    - 显式 --user-id：查存在性（不存在则报错退出，避免污染外键）
    - --username（默认）：先按 username 查 users 表；查不到则新建（legacy_int_id=NULL）
    """
    if user_id:
        with v2_read_conn() as conn:
            row = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise SystemExit(f"[ERR] --user-id {user_id!r} 在 V2 users 表中不存在；请先手动创建或改用 --username")
        logger.info("使用显式 V2 user: id=%s username=%s", row["id"], row["username"])
        return row["id"]

    uname = username or "cli_real_run"
    with v2_read_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (uname,)).fetchone()
    if row is not None:
        logger.info("复用已存在的 V2 user: username=%s id=%s", uname, row["id"])
        return row["id"]

    uid = new_ulid()
    repo.save_user(uid, uname, password_hash="")  # CLI 场景不做认证，password_hash 留空
    logger.info("已自动开通 V2 user: username=%s id=%s", uname, uid)
    return uid


# ============================================================
# 输出 JSON（含 review/optimizer 摘要 + 白名单 config_snapshot）
# ============================================================


def _jsonable(obj):
    """core/v2 产物 → JSON 安全结构（复用 web/v2_service 同款递归；避免 CLI 依赖 Flask）。

    支持：Enum / Pydantic BaseModel / dataclass / SimpleNamespace / datetime / list / dict / 基本型。
    """
    from dataclasses import fields, is_dataclass
    from enum import Enum
    from types import SimpleNamespace

    from pydantic import BaseModel

    if isinstance(obj, Enum):
        return obj.value
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, SimpleNamespace):
        return {k: _jsonable(v) for k, v in vars(obj).items()}
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    return str(obj)


def build_output_payload(pipeline_result, run_id: str | None) -> dict:
    """PipelineResult + Run + ReviewReport + OptimizerResult → output JSON payload。

    ★ config_snapshot 走白名单；steps 里的 artifact_ids 只含 doc_id/version_id/run_id（无敏感）。
    """
    payload: dict = {
        "run_id": pipeline_result.run_id,
        "success": pipeline_result.success,
        "failed_step": pipeline_result.failed_step,
        "error_message": pipeline_result.error_message,
        "total_duration_ms": pipeline_result.total_duration_ms,
        "total_llm_calls": pipeline_result.total_llm_calls,
        "doc_id": pipeline_result.doc_id,
        "version_id": pipeline_result.version_id,
        "test_point_count": pipeline_result.test_point_count,
        "test_case_count": pipeline_result.test_case_count,
        "review_report_id": pipeline_result.review_report_id,
        "steps": [_jsonable(s) for s in pipeline_result.steps],
        "config_snapshot": build_config_snapshot(),
        "generated_at": datetime.now(UTC).isoformat(),
    }

    # Run 最终状态与 counts（从 DB 读，反映 Runtime 已收尾）
    if pipeline_result.run_id:
        run = repo.get_run(pipeline_result.run_id)
        if run is not None:
            payload["run_status"] = _jsonable(run.status)
            payload["counts"] = _jsonable(run.counts)
            payload["run_failed_step"] = run.failed_step
            payload["run_error_message"] = run.error_message

    # ReviewReport 6 维评分与 findings 摘要（get_review_report 已一并返回 findings，无需二次查）
    if pipeline_result.review_report_id:
        report = repo.get_review_report(pipeline_result.review_report_id)
        if report is not None:
            findings = list(report.findings or [])
            payload["review_summary"] = {
                "scores": _jsonable(report.scores),
                "coverage_detail": _jsonable(report.coverage_detail) if report.coverage_detail else None,
                "executability_detail": _jsonable(report.executability_detail) if report.executability_detail else None,
                "finding_count": len(findings),
                "auto_fixable_count": sum(1 for f in findings if _finding_auto_fixable(f)),
                "findings_by_dimension": _group_findings_by_dimension(findings),
            }

    # OptimizerResult 内存对象（Runtime 已透传；不持久化，落 JSON 供审计）
    if pipeline_result.optimizer_result is not None:
        op = pipeline_result.optimizer_result
        payload["optimizer_summary"] = {
            "processed_findings": op.processed_findings,
            "archived_cases": op.archived_cases,
            "skipped_findings": op.skipped_findings,
            "actions_count": len(op.actions),
            "skip_reasons": _jsonable(op.skip_reasons),
        }

    # 兜底：payload 里若混入敏感键（不应发生），assert_no_sensitive 会拦下
    return payload


def _finding_auto_fixable(f) -> bool:
    """finding 可能是 Pydantic 对象或已 dump 的 dict（get_review_report 内做了 model_dump）；两者兼容。"""
    if isinstance(f, dict):
        return bool(f.get("auto_fixable"))
    return bool(getattr(f, "auto_fixable", False))


def _group_findings_by_dimension(findings) -> dict[str, int]:
    """按 dimension 分组统计 finding 数（供快速看"哪个维度问题最多"）。"""
    grouped: dict[str, int] = {}
    for f in findings:
        key = f.get("dimension") if isinstance(f, dict) else getattr(f, "dimension", None)
        key = key.value if hasattr(key, "value") else str(key) if key else "unknown"
        grouped[key] = grouped.get(key, 0) + 1
    return grouped


def write_output_json(payload: dict, output_dir: Path, run_id: str | None) -> Path:
    """把 payload 写到 output/v2_real_run_<run_id>.json；写盘前跑 assert_no_sensitive 自检。

    run_id 为 None（stop_after=ir 或 ir 阶段失败）时，用时间戳兜底文件名。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ_no_run")
    path = output_dir / f"v2_real_run_{suffix}.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    assert_no_sensitive(text)  # ★ 双保险
    path.write_text(text, encoding="utf-8")
    return path


# ============================================================
# 控制台汇总（人读）
# ============================================================


def print_console_summary(payload: dict, json_path: Path) -> None:
    """结尾打印人读汇总：run_id / 状态 / 各阶段耗时 / 计数 / 评分 / 归档 / JSON 路径。"""
    line = "=" * 72
    print(f"\n{line}\nV2 真实运行汇总（Step 10.5）\n{line}")
    print(f"run_id       : {payload.get('run_id')}")
    print(f"success      : {payload.get('success')}")
    print(f"run_status   : {payload.get('run_status')}")
    if payload.get("failed_step"):
        print(f"failed_step  : {payload.get('failed_step')}")
        print(f"error        : {payload.get('error_message')}")
    print(f"total_dur_ms : {payload.get('total_duration_ms')}")
    print(f"llm_calls    : {payload.get('total_llm_calls')}")
    print(f"doc/version  : {payload.get('doc_id')} / {payload.get('version_id')}")
    print(f"TP/TC count  : {payload.get('test_point_count')} / {payload.get('test_case_count')}")
    if payload.get("counts"):
        c = payload["counts"]
        print(
            f"Run.counts   : items={c.get('items')} points={c.get('points')} "
            f"cases={c.get('cases')} obligations={c.get('obligations')}"
        )

    print("\n--- 各阶段耗时 ---")
    for s in payload.get("steps", []):
        # ★ 用 ASCII 标记代替 emoji：Windows PowerShell 默认 GBK 编码，✅/❌ 会触发 UnicodeEncodeError
        marker = "[OK]" if s.get("success") else "[FAIL]"
        print(
            f"  {marker} {s.get('step'):<12} {s.get('duration_ms'):>6} ms  "
            f"llm_calls={s.get('llm_calls', 0):<3} counts={s.get('counts')}"
        )

    if payload.get("review_summary"):
        rs = payload["review_summary"]
        print("\n--- Review 6 维评分 ---")
        for k, v in (rs.get("scores") or {}).items():
            print(f"  {k:<20}: {v}")
        print(f"  findings           : {rs.get('finding_count')} (auto_fixable={rs.get('auto_fixable_count')})")
        print(f"  by dimension       : {rs.get('findings_by_dimension')}")
        if rs.get("coverage_detail"):
            cd = rs["coverage_detail"]
            print(f"  strategy_coverage  : {cd.get('strategy_obligation_coverage')}")
            print(f"  item_coverage      : {cd.get('requirement_item_coverage')}")
            print(f"  uncovered_items    : {len(cd.get('uncovered_item_ids') or [])}")

    if payload.get("optimizer_summary"):
        os_ = payload["optimizer_summary"]
        print("\n--- Optimizer 归档 ---")
        print(
            f"  processed={os_.get('processed_findings')} archived={os_.get('archived_cases')} "
            f"skipped={os_.get('skipped_findings')}"
        )

    print(f"\noutput JSON  : {json_path}")
    print(line)


# ============================================================
# 参数解析
# ============================================================


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="v2_real_run",
        description="V2 真实 LLM 全链路 CLI（Step 10.5）—— 权威真实运行验证路径",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0=成功/dry-run；1=Pipeline 失败；2=参数错误；3=Bootstrap 失败",
    )
    src = p.add_argument_group("需求来源（--requirement 与 --text 二选一）")
    src.add_argument("--requirement", type=str, help="需求文件路径（.md/.txt/.xlsx/图片）")
    src.add_argument("--text", type=str, help="需求文本（inline，与 --requirement 二选一）")
    src.add_argument("--title", type=str, default="V2 CLI 真实运行", help="Run/Doc 标题")
    src.add_argument(
        "--source-type",
        type=str,
        choices=[s.value for s in SourceType],
        default=None,
        help="需求来源类型（默认按 --requirement 扩展名推断；--text 走 text）",
    )

    user = p.add_argument_group("V2 user")
    user.add_argument("--username", type=str, default="cli_real_run", help="按 username 查/建 V2 user（默认）")
    user.add_argument("--user-id", type=str, default=None, help="显式指定 V2 user ULID（存在性校验）")

    run = p.add_argument_group("运行控制")
    run.add_argument(
        "--stop-after",
        type=str,
        choices=list(STEP_ORDER),
        default=None,
        help=f"跑到指定阶段后停（透传 Runtime；合法值 {STEP_ORDER}）",
    )
    run.add_argument("--output-dir", type=str, default=str(_PROJECT_ROOT / "output"), help="output JSON 落盘目录")
    run.add_argument("--dry-run", action="store_true", help="只打印参数与配置摘要，不读文件/不建 user/不碰 DB/不调 LLM")
    run.add_argument("--verbose", action="store_true", help="打开 DEBUG 日志")
    return p


def _infer_source_type(args: argparse.Namespace) -> SourceType:
    """按 --source-type 显式值 > --requirement 扩展名 > --text 默认 TEXT 推断。"""
    if args.source_type:
        return SourceType(args.source_type)
    if args.requirement:
        suffix = Path(args.requirement).suffix.lower()
        mapping = {
            ".md": SourceType.MARKDOWN,
            ".markdown": SourceType.MARKDOWN,
            ".txt": SourceType.TEXT,
            ".xlsx": SourceType.EXCEL,
            ".xls": SourceType.EXCEL,
            ".png": SourceType.IMAGE,
            ".jpg": SourceType.IMAGE,
            ".jpeg": SourceType.IMAGE,
        }
        return mapping.get(suffix, SourceType.MARKDOWN)
    return SourceType.TEXT


def _validate_args(args: argparse.Namespace) -> None:
    """argparse 之后的组合校验：--requirement 与 --text 必须给且仅给一个。"""
    if bool(args.requirement) == bool(args.text):
        raise SystemExit("[ERR] --requirement 与 --text 必须给且仅给一个")
    if args.requirement and not Path(args.requirement).exists():
        raise SystemExit(f"[ERR] --requirement 文件不存在: {args.requirement}")
    if args.user_id and args.username != "cli_real_run":
        # 显式 user-id 时 username 无意义，warn 但不 fail
        logger.warning("--user-id 与 --username 同时给出，以 --user-id 为准")


# ============================================================
# 主流程
# ============================================================


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # 抑制 openai/httpx 的 DEBUG 噪声（防止意外打印 header）
    for noisy in ("openai", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def do_dry_run(args: argparse.Namespace) -> int:
    """轻量 dry-run：只打印参数与脱敏配置摘要，不读文件/不建 user/不碰 DB/不调 LLM。"""
    print("\n" + "=" * 72 + "\nDRY-RUN（不读文件 / 不建 user / 不碰 DB / 不调 LLM）\n" + "=" * 72)
    print(f"requirement   : {args.requirement}")
    print(f"text          : {args.text!r}"[:120] + ("..." if args.text and len(args.text) > 100 else ""))
    print(f"title         : {args.title}")
    print(f"source_type   : {_infer_source_type(args).value}")
    print(f"username      : {args.username}")
    print(f"user_id       : {args.user_id}")
    print(f"stop_after    : {args.stop_after}")
    print(f"output_dir    : {args.output_dir}")

    snapshot = build_config_snapshot()
    snapshot_str = json.dumps(snapshot, ensure_ascii=False, indent=2)
    assert_no_sensitive(snapshot_str)  # 双保险
    print("\n--- config_snapshot（白名单，已 assert 无敏感字段）---")
    print(snapshot_str)
    print("=" * 72 + "\n")
    return 0


def run_cli(args: argparse.Namespace) -> int:
    """真实运行主流程。返回退出码。"""
    # 1. Bootstrap V2 DB（幂等；失败退出码 3）
    try:
        ensure_v2_ready()
    except V2BootstrapError as e:
        logger.error("V2 bootstrap 失败: %s", e)
        return 3

    # 2. 解析 V2 user
    v2_uid = resolve_v2_user(args.username, args.user_id)

    # 3. 构造 paths/text
    paths = [args.requirement] if args.requirement else None
    text = args.text if args.text else None
    source_type = _infer_source_type(args)

    logger.info(
        "启动 V2 全链路: title=%r source_type=%s stop_after=%s paths=%s text_len=%s",
        args.title,
        source_type.value,
        args.stop_after,
        paths,
        len(text or ""),
    )

    # 4. 打印脱敏配置摘要（INFO 级别，供运行前审计）
    snapshot = build_config_snapshot()
    logger.info("LLM 配置摘要（白名单）: %s", json.dumps(snapshot["llm"], ensure_ascii=False))
    logger.info("Prompt 版本: %s", json.dumps(snapshot["prompt_versions"], ensure_ascii=False))

    # 5. 调 Runtime（不传 client，让 client_factory 按用途构建 generate/review 双客户端）
    result = run_v2_pipeline(
        user_id=v2_uid,
        title=args.title,
        text=text,
        paths=paths,
        source_type=source_type,
        stop_after=args.stop_after,
    )

    # 6. 落 output JSON（含 api_key 自检）
    payload = build_output_payload(result, result.run_id)
    json_path = write_output_json(payload, Path(args.output_dir), result.run_id)

    # 7. 控制台汇总
    print_console_summary(payload, json_path)

    # 8. 退出码
    return 0 if result.success else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    try:
        _validate_args(args)
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2

    if args.dry_run:
        return do_dry_run(args)
    return run_cli(args)


if __name__ == "__main__":
    sys.exit(main())
