"""V2 Step 11 S8 Compare / Delta CLI —— 只读比较器（设计文档 §11 + 附录 D）。

两种模式（同一进程内互斥）：

  比较（默认）::
      python scripts/v2_benchmark_compare.py \\
          --baseline  benchmark/baselines/baseline-v0.1.json \\
          --candidate benchmark/runsets/<runset_id> \\
          --out       benchmark/compares/baseline-v0.1__vs__<runset_id>.json

  提升 baseline（``--promote-baseline``）::
      python scripts/v2_benchmark_compare.py --promote-baseline \\
          --baseline benchmark/runsets/<runset_id> \\
          --out      benchmark/baselines/baseline-v0.1.json

★ 三条纪律：
  1. **只读**：不跑 Runtime、不调 LLM、不碰任何 DB（含 benchmark DB）、不改 benchmark 数据集；
  2. **不做门禁**：regression 只是报告里的一栏，**绝不因质量下降返回非 0**（§11.3）；
  3. **不越 S6 冻结面**：本文件是独立入口，`scripts/v2_benchmark.py` 一行不改。

退出码：0=报告/基线成功产出 / 2=参数错误 / 3=操作数缺失或不完整 / 5=落盘或敏感自检失败
（**没有 1**：1 在 S6 里表示"存在非 COMPLETED"，S8 刻意不沿用，以免变成隐式门禁）
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.v2.eval import compare as cp  # noqa: E402
from core.v2.eval import runner_lib as rl  # noqa: E402

logger = logging.getLogger("v2.benchmark.compare")

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_OPERAND = 3
EXIT_WRITE_FAILURE = 5


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="v2_benchmark_compare",
        description="V2 Step 11 S8 Compare/Delta（只读、零 LLM、无门禁）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0=成功产出 / 2=参数错误 / 3=操作数缺失或不完整 / 5=落盘或敏感自检失败。regression 不影响退出码。",
    )
    p.add_argument("--baseline", required=True, help="baseline JSON，或 --promote-baseline 模式下的 runset 目录")
    p.add_argument("--candidate", default=None, help="待比较的 runset 目录（比较模式必填）")
    p.add_argument("--out", default=None, help="输出路径（缺省见各模式说明）")
    p.add_argument(
        "--promote-baseline",
        action="store_true",
        help="提升模式：把 --baseline 指向的 runset 提升为机器可读 baseline JSON 写到 --out",
    )
    p.add_argument(
        "--baseline-id",
        default=cp.DEFAULT_BASELINE_ID,
        help=f"提升模式使用的 baseline_id（默认 {cp.DEFAULT_BASELINE_ID}）",
    )
    p.add_argument("--verbose", action="store_true", help="DEBUG 日志")
    return p


def validate_args(args: argparse.Namespace) -> str | None:
    """组合校验（返回错误串，None=通过）。错误一律 → EXIT_USAGE。"""
    if args.promote_baseline:
        if args.candidate:
            return "--promote-baseline 模式不接受 --candidate（该模式只产 baseline，不做比较）"
        if not Path(args.baseline).is_dir():
            return f"--promote-baseline 需要 --baseline 指向一个 runset 目录，实得: {args.baseline}"
        return None
    if not args.candidate:
        return "比较模式必须给出 --candidate <runset 目录>（或使用 --promote-baseline 提升基线）"
    return None


def default_compare_out(baseline_rec: dict, candidate_rec: dict) -> Path:
    btag = baseline_rec.get("baseline_id") or baseline_rec.get("runset_id") or "baseline"
    return _PROJECT_ROOT / cp.DEFAULT_COMPARE_DIR / f"{btag}__vs__{candidate_rec.get('runset_id') or 'candidate'}.json"


def do_promote(args: argparse.Namespace) -> int:
    out = Path(args.out) if args.out else (_PROJECT_ROOT / cp.DEFAULT_BASELINE_PATH)
    payload = cp.emit_baseline(args.baseline, out, baseline_id=args.baseline_id)
    agg = payload["aggregate"]
    print("\n" + "=" * 78 + "\nPROMOTE BASELINE（只读 runset 落盘产物，零 LLM / 零 DB）\n" + "=" * 78)
    print(f"  source runset  : {payload['source_runset_id']}")
    print(f"  baseline_id    : {payload['baseline_id']}   s8_schema: {payload['s8_schema']}")
    print(
        f"  model          : {payload['fingerprint'].get('model_name')} / {payload['fingerprint'].get('model_provider')}"
    )
    print(
        f"  git            : {str(payload['fingerprint'].get('git_commit'))[:12]} dirty={payload['fingerprint'].get('git_dirty')}"
    )
    print(f"  case_set_digest: {payload['fingerprint'].get('case_set_digest')}")
    print(
        f"  cases          : {agg['cases_total']}  completed={agg['status_counts'].get('completed')}  "
        f"completion_rate={agg['completion_rate']}"
    )
    print(
        f"  llm calls      : total={agg['totals']['llm_calls_total']} "
        f"(pipeline={agg['totals']['llm_calls_pipeline']} + s5={agg['totals']['llm_calls_s5']}) "
        f"retries={agg['totals']['llm_retries']} failures={agg['totals']['llm_failures']}"
    )
    print(f"  duration       : {agg['totals']['total_duration_ms'] / 1000:.0f}s")
    print(f"  written        : {out}")
    print("=" * 78)
    return EXIT_OK


def do_compare(args: argparse.Namespace) -> int:
    baseline = cp.resolve_operand(args.baseline)
    candidate = cp.resolve_operand(args.candidate)
    report = cp.compare_runsets(baseline, candidate)
    out = Path(args.out) if args.out else default_compare_out(baseline, candidate)
    rl.write_json_atomic(out, report)

    print("\n" + "=" * 78 + "\nS8 COMPARE / DELTA（无门禁：regression 不影响退出码）\n" + "=" * 78)
    print(
        f"  baseline  : {report['baseline']['kind']} {report['baseline'].get('baseline_id') or report['baseline']['runset_id']}"
        f"  model={report['baseline']['model_name']}  completed={report['baseline']['completed_cases']}/{report['baseline']['cases_total']}"
    )
    print(
        f"  candidate : {report['candidate']['kind']} {report['candidate']['runset_id']}"
        f"  model={report['candidate']['model_name']}  completed={report['candidate']['completed_cases']}/{report['candidate']['cases_total']}"
    )
    print(f"  git       : {report['baseline']['git_commit']} → {report['candidate']['git_commit']}")
    print("-" * 78)
    for line in cp.summarize_report(report):
        print(line)
    print("-" * 78)
    if not report["comparability"]["comparable"]:
        print("  ⚠ NOT COMPARABLE：以上差值仅为原始读数对照，不得据此下质量结论（附录 D.3）。")
    print(f"  written   : {out}")
    print("=" * 78)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    for noisy in ("openai", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    err = validate_args(args)
    if err:
        print(f"[ERR] {err}", file=sys.stderr)
        return EXIT_USAGE
    try:
        return do_promote(args) if args.promote_baseline else do_compare(args)
    except cp.CompareOperandError as exc:
        print(f"[ERR] 操作数不可用: {exc}", file=sys.stderr)
        return EXIT_OPERAND
    except rl.SensitiveDataError as exc:
        print(f"[ERR] 敏感自检失败，拒绝落盘: {exc}", file=sys.stderr)
        return EXIT_WRITE_FAILURE
    except OSError as exc:
        print(f"[ERR] 落盘失败: {type(exc).__name__}", file=sys.stderr)
        return EXIT_WRITE_FAILURE
    except KeyboardInterrupt:  # pragma: no cover - 人工中断
        logger.warning("已被用户中断")
        return EXIT_WRITE_FAILURE


if __name__ == "__main__":
    sys.exit(main())
