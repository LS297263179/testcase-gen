"""V2 Step 11 S5 语义评价层 - 消费 RunObservation + HardMetricsReport，产出可序列化 SoftMetricsReport。

设计基线：docs/v2/step11-benchmark-evaluation.md（v1.0）+ S5 架构拍板（2026-09-21）。
定位：Runtime 外部观测层的语义轨道。零 DB 依赖（证据校验用纯内存白名单，禁复用 Step7
TargetResolver）、零 Runtime 依赖、client=None 时不猜分、结果 JSON-safe 可被 S6/S7 直接读取。

双轨严格分离（冻结）：
  - GOLD-BASED：本模块 semantic accuracy / forbidden_suspect / additional_candidate（provenance=LLM）；
  - REVIEWER-BASED：hard_report.reviewer_based 原样透传，S5 任何计算路径不读取、不重算、不合并。

判定治理（代码强制，不依赖模型自觉）：
  - 只有 AUTO_HIT Gold trace 可靠的 TC 送 LLM；无可靠 trace → PENDING_REVIEW(kind=no_reliable_trace)；
  - uncertain / confidence<0.6 / INCORRECT 证据不足 / batch 失败 / 结果缺失 → PENDING_REVIEW；
  - Semantic Accuracy = CORRECT/(CORRECT+PARTIAL+INCORRECT)，PENDING 不进分子分母；分母 0 → value=None；
  - forbidden 疑似 → ForbiddenSuspect + pending_review(kind=forbidden_suspect)，永不写 S3 invalid；
  - Gold 外合理新增 → AdditionalValidCandidate(status=suggested)，无终态裁决权。

批次契约（S6 可重放）：display_id 稳定排序 → 超长 TC 独占批 → 每批 ≤BATCH_SIZE →
payload 超 MAX_PAYLOAD_CHARS 按稳定顺序对半拆分；同输入必产生同批次。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum

from core.v2.eval.matching import ItemMatchState
from core.v2.eval.metrics_hard import (
    HardMetricsReport,
    MetricCell,
    PendingReviewItem,
    ReviewerBasedSnapshot,
    RunObservation,
)
from core.v2.eval.schema import BenchmarkGold, MetricProvenance
from core.v2.eval.semantic_prompts import SEMANTIC_EVAL_PROMPT, SEMANTIC_EVAL_PROMPT_VERSION
from core.v2.parser import extract_json

logger = logging.getLogger("v2.eval.metrics_soft")

# ============================================================
# 冻结常量
# ============================================================

S5_SCHEMA_VERSION = "benchmark-soft-v1"
BATCH_SIZE = 20  # 每批最多 TestCase 数
MAX_SINGLE_TC_CHARS = 6000  # 单条 TC 序列化超过则独占一批
MAX_PAYLOAD_CHARS = 24000  # 单批 payload 字符上限，超出按稳定顺序对半拆分
LOW_CONFIDENCE = 0.6  # 低于此值一律 PENDING；INCORRECT 门槛
PENDING_BATCH_SIZE = 40  # 裁决批（pending items）分块大小

_VERDICT_MAP = {"correct": "CORRECT", "partial": "PARTIAL", "incorrect": "INCORRECT", "uncertain": "PENDING"}
_VALID_TARGET_TYPES = {"testcase", "testpoint", "item"}


class SemanticVerdict(StrEnum):
    """S5 语义判定四态（LLM uncertain 与各类降级统一映射到 PENDING_REVIEW）。"""

    CORRECT = "correct"
    PARTIAL = "partial"
    INCORRECT = "incorrect"
    PENDING_REVIEW = "pending_review"


# ============================================================
# 结果数据结构（全部 JSON-safe）
# ============================================================


@dataclass
class TcJudgment:
    """单条 TestCase 的语义判定。gold_ids 由代码从 trace 注入（非 LLM 输出）。"""

    tc_id: str
    display_id: str
    gold_ids: list[str] = field(default_factory=list)
    verdict: SemanticVerdict = SemanticVerdict.PENDING_REVIEW
    confidence: float | None = None
    reason: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    forbidden_pattern_id: str | None = None
    batch_index: int = -1
    provenance: MetricProvenance = MetricProvenance.LLM
    pending_reason: str | None = (
        None  # uncertain/low_confidence/insufficient_evidence/no_reliable_trace/batch_failure/missing_result/no_client
    )


@dataclass
class ForbiddenSuspect:
    """LLM 疑似命中 forbidden pattern（建议权，无裁决权；永不写入 S3 invalid）。"""

    pattern_id: str
    tc_id: str
    display_id: str
    reason: str = ""
    confidence: float | None = None
    provenance: MetricProvenance = MetricProvenance.LLM


@dataclass
class AdditionalValidCandidate:
    """Gold 外合理新增的预审建议；status 恒为 suggested，终审权在人。"""

    target_type: str  # testcase | testpoint | item
    ref_id: str
    display_id: str | None = None
    reason: str = ""
    confidence: float | None = None
    evidence_refs: list[str] = field(default_factory=list)
    status: str = "suggested"
    provenance: MetricProvenance = MetricProvenance.LLM


@dataclass
class BatchMeta:
    """单批调用元数据（失败隔离证据 + 可重放对账）。

    failure_kind 与 evidence 用于区分"批次失败"与"条目待人工"，并留下最小可诊断证据：
      - transport：chat() 抛异常（网络/认证/额度），无响应正文；
      - protocol：拿到响应但结构非法（空正文 / 不可解析 / 缺 results）；
      - partial：批次整体成功，但有条目被治理丢弃或未返回；
      - 空串：完全正常。
    evidence 只含长度 / SHA-256 摘要 / 形状 / 计数，**绝不含响应原文**（敏感信息禁令 §10.1）。
    """

    index: int
    kind: str  # "tc_batch" | "pending_batch"
    n_cases: int
    ok: bool = True
    llm_calls: int = 0
    error: str | None = None
    refs: list[str] = field(default_factory=list)
    failure_kind: str = ""
    evidence: dict = field(default_factory=dict)


def _response_shape(raw: str | None, data: object) -> str:
    """响应形状分类：只依据"有没有正文 / 能否解析 / 有无 results"，不返回任何内容。"""
    if raw is None:
        return "no_response"
    if not raw.strip():
        return "empty_body"
    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            return "json_with_results"
        return "json_without_results"
    if data is None:
        return "json_unparsable"
    return "json_not_object"


def _batch_evidence(raw: str | None, data: object = None, dropped: int = 0, unreturned: int = 0) -> dict:
    """批次最小可诊断证据（长度 + 摘要 + 形状 + 计数）。空正文不产摘要，避免把 sha256('') 当线索。"""
    text = raw or ""
    ev: dict = {"shape": _response_shape(raw, data), "raw_len": len(text)}
    if text:
        ev["raw_sha256_12"] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    if dropped:
        ev["dropped_items"] = dropped
    if unreturned:
        ev["unreturned_cases"] = unreturned
    return ev


@dataclass
class SoftMetricsReport:
    """S5 顶层产物（GOLD-BASED 语义轨道 + 原样透传的 REVIEWER-BASED）。"""

    case_id: str
    gold_version: str
    run_id: str | None = None
    s5_prompt_version: str = SEMANTIC_EVAL_PROMPT_VERSION
    model_meta: dict = field(default_factory=dict)
    semantic_accuracy: MetricCell = field(default_factory=lambda: MetricCell(provenance=MetricProvenance.LLM))
    verdict_counts: dict = field(default_factory=dict)
    judgments: list[TcJudgment] = field(default_factory=list)
    forbidden_suspects: list[ForbiddenSuspect] = field(default_factory=list)
    additional_candidates: list[AdditionalValidCandidate] = field(default_factory=list)
    pending_review: list[PendingReviewItem] = field(default_factory=list)  # S3 清单复制 + S5 新增登记
    batches: list[BatchMeta] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    llm_calls: int = 0
    reviewer_based: ReviewerBasedSnapshot | None = None  # 透传：S5 任何计算禁止读取该字段


# ============================================================
# 内部工具：白名单证据校验 / payload 构造 / 确定性分批
# ============================================================


@dataclass
class _Evidence:
    """纯内存证据白名单（S5 零 DB 依赖的关键；禁复用 Step7 TargetResolver）。"""

    gold_ids: set[str]
    pattern_ids: set[str]
    tc_ids: set[str]
    display_ids: set[str]
    tp_ids: set[str]
    item_ids: set[str]

    def valid_ref(self, ref: object) -> bool:
        s = str(ref or "")
        return (
            s in self.gold_ids
            or s in self.pattern_ids
            or s in self.tc_ids
            or s in self.display_ids
            or s in self.tp_ids
            or s in self.item_ids
        )

    def validate_refs(self, refs: object) -> tuple[list[str], list[str]]:
        if not isinstance(refs, list):
            return [], [str(x) for x in (refs or [])] if refs else []
        kept = [str(r) for r in refs if self.valid_ref(r)]
        dropped = [str(r) for r in refs if not self.valid_ref(r)]
        return kept, dropped


def _parse_confidence(raw: object) -> float | None:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if 0.0 <= v <= 1.0 else None


def _tc_entry(tc, gold_ids: list[str], tp_by_id: dict) -> dict:
    points = []
    for pid in tc.test_point_ids:
        tp = tp_by_id.get(pid)
        if tp is not None:
            tech = tp.technique.value if tp.technique else None
            points.append(
                {
                    "module": tp.module,
                    "subcategory": tp.subcategory,
                    "technique": tech,
                    "obligation_id": tp.obligation_id,
                }
            )
    return {
        "id": tc.id,
        "display_id": tc.display_id,
        "module": tc.module,
        "title": tc.title,
        "precondition": tc.precondition,
        "steps": [s.model_dump(mode="json") for s in tc.steps],
        "expected": tc.expected,
        "priority": tc.priority.value,
        "type": tc.type.value,
        "trace": {"gold_ids": sorted(gold_ids), "points": points},
    }


def _gold_context(gold: BenchmarkGold, gold_ids: set[str]) -> dict:
    """批上下文（设计 §14）：本批 trace 到的 Gold 子集 + 相关场景/义务 + 全量 forbidden_patterns。"""
    return {
        "gold_requirements": [
            {"gold_id": g.gold_id, "module": g.module, "type": g.type.value, "statement": g.statement}
            for g in gold.gold_requirements
            if g.gold_id in gold_ids
        ],
        "critical_scenarios": [
            {
                "scenario_id": s.scenario_id,
                "title": s.title,
                "requirement_gold_ids": s.requirement_gold_ids,
                "expected_techniques": [t.value for t in s.expected_techniques],
                "expected_outcomes": s.expected_outcomes,
            }
            for s in gold.critical_scenarios
            if set(s.requirement_gold_ids) & gold_ids
        ],
        "obligations_expected": [
            {
                "target": o.target,
                "technique": o.technique.value,
                "description": o.description,
                "requirement_gold_id": o.requirement_gold_id,
            }
            for o in gold.obligations_expected
            if o.requirement_gold_id is None or o.requirement_gold_id in gold_ids
        ],
        "forbidden_patterns": [
            {"pattern_id": fp.pattern_id, "description": fp.description} for fp in gold.forbidden_patterns
        ],
    }


def _batch_payload(entries: list[dict], gold: BenchmarkGold) -> str:
    gold_ids = {gid for e in entries for gid in e["trace"]["gold_ids"]}
    body = _gold_context(gold, gold_ids)
    body["testcases"] = entries
    body["pending_items"] = []
    return json.dumps(body, ensure_ascii=False)


def _pending_payload(gold: BenchmarkGold, entries: list[dict], arts) -> str:
    """裁决批上下文：S3 登记的 Gold 外产物（unverified_output/unsupported_item）+ 全量 Gold 摘要。"""
    tp_by_id = {tp.id: tp for tp in arts.test_points}
    item_by_id = {it.id: it for it in arts.items}
    tc_by_id = {tc.id: tc for tc in arts.test_cases}
    pending_items = []
    for p in entries:
        ctx: dict = {"kind": p.kind, "ref_id": p.ref_id, "detail": p.detail}
        if p.kind == "unverified_output" and p.ref_id in tp_by_id:
            tp = tp_by_id[p.ref_id]
            ctx["artifact"] = {
                "module": tp.module,
                "subcategory": tp.subcategory,
                "title": tp.title,
                "description": tp.description,
            }
        elif p.kind == "unsupported_item" and p.ref_id in item_by_id:
            it = item_by_id[p.ref_id]
            ctx["artifact"] = {"module": it.module, "type": it.type.value, "statement": it.statement}
        elif p.kind == "testcase" and p.ref_id in tc_by_id:
            ctx["artifact"] = {"display_id": tc_by_id[p.ref_id].display_id, "title": tc_by_id[p.ref_id].title}
        pending_items.append(ctx)
    body = _gold_context(gold, {g.gold_id for g in gold.gold_requirements})
    body["testcases"] = []
    body["pending_items"] = pending_items
    return json.dumps(body, ensure_ascii=False)


def _make_batches(entries: list[dict], gold: BenchmarkGold) -> list[list[dict]]:
    """确定性分批（冻结规则，同输入同批次，S6 可重放）：
    1) 单条序列化 > MAX_SINGLE_TC_CHARS → 独占批；2) 其余按序每 BATCH_SIZE 一批；
    3) 批 payload > MAX_PAYLOAD_CHARS → 按稳定顺序对半递归拆分。"""
    singles = [e for e in entries if len(json.dumps(e, ensure_ascii=False)) > MAX_SINGLE_TC_CHARS]
    normal = [e for e in entries if len(json.dumps(e, ensure_ascii=False)) <= MAX_SINGLE_TC_CHARS]
    chunks: list[list[dict]] = [[e] for e in singles]
    chunks += [normal[i : i + BATCH_SIZE] for i in range(0, len(normal), BATCH_SIZE)]
    out: list[list[dict]] = []

    def _rec(chunk: list[dict]) -> None:
        if not chunk:
            return
        if len(chunk) == 1 or len(_batch_payload(chunk, gold)) <= MAX_PAYLOAD_CHARS:
            out.append(chunk)
            return
        mid = len(chunk) // 2
        _rec(chunk[:mid])
        _rec(chunk[mid:])

    for ch in chunks:
        _rec(ch)
    return out


# ============================================================
# 主入口
# ============================================================


def additional_pool_refs(hard_report: HardMetricsReport) -> dict[str, str]:
    """裁决批允许被建议引用的对象池（按 kind 取首个：item→unsupported_item，testpoint→unverified_output）。"""
    pool: dict[str, str] = {}
    for p in hard_report.pending_review:
        if p.kind == "unsupported_item" and "item" not in pool:
            pool["item"] = p.ref_id
        elif p.kind == "unverified_output" and "testpoint" not in pool:
            pool["testpoint"] = p.ref_id
    return pool


def evaluate_soft_metrics(
    obs: RunObservation,
    hard_report: HardMetricsReport,
    client=None,
) -> SoftMetricsReport:
    """S5 语义评价入口。client=None 时不猜分（全部 PENDING_REVIEW，semantic 格 value=None）。

    输入：RunObservation（含 Gold + 产物）+ S3 HardMetricsReport（只读，绝不修改）。
    LLM 调用：每批一次 client.chat(SEMANTIC_EVAL_PROMPT, payload)；失败批整体降级，其余批继续。
    """
    gold = obs.gold
    arts = obs.artifacts
    tp_by_id = {tp.id: tp for tp in arts.test_points}

    wl = _Evidence(
        gold_ids={g.gold_id for g in gold.gold_requirements},
        pattern_ids={fp.pattern_id for fp in gold.forbidden_patterns},
        tc_ids={tc.id for tc in arts.test_cases},
        display_ids={tc.display_id for tc in arts.test_cases},
        tp_ids=set(tp_by_id),
        item_ids={it.id for it in arts.items},
    )

    # AUTO_HIT 映射：item_id → gold_ids；CANDIDATE/AMBIGUOUS 的候选 item 视作"匹配未定"
    hit_item_to_golds: dict[str, list[str]] = {}
    undetermined_items: set[str] = set()
    for m in hard_report.requirement_matches:
        if m.state == ItemMatchState.AUTO_HIT and m.item_id:
            hit_item_to_golds.setdefault(m.item_id, []).append(m.gold_id)
        elif m.state in (ItemMatchState.CANDIDATE, ItemMatchState.AMBIGUOUS):
            undetermined_items.update(m.item_ids)

    invalid_tc_ids = {f.target_id for f in hard_report.invalid if f.target_type == "testcase"}

    report = SoftMetricsReport(
        case_id=obs.case_id,
        gold_version=gold.gold_version,
        run_id=obs.run_id,
        reviewer_based=hard_report.reviewer_based,  # 纯透传，不参与任何计算
    )
    # S3 pending 清单【复制】登记（绝不修改 hard_report 对象本身），S5 新增项继续 append 到副本
    report.pending_review = [PendingReviewItem(p.kind, p.ref_id, p.detail) for p in hard_report.pending_review]
    cfg = obs.generation_config
    if cfg is not None:  # 白名单四字段（§18），凭证严禁入内
        report.model_meta = {
            "provider": cfg.model_provider,
            "model": cfg.model_name,
            "temperature": cfg.temperature,
            "enable_thinking": cfg.enable_thinking,
        }

    entries: list[dict] = []
    for tc in arts.test_cases:
        if tc.id in invalid_tc_ids:
            continue  # S3 CODE 已判 INVALID 的不再做语义评价（避免双重计数）
        gids = sorted(
            {
                gid
                for pid in tc.test_point_ids
                if (tp := tp_by_id.get(pid)) is not None
                for iid in tp.item_ids
                for gid in hit_item_to_golds.get(iid, [])
            }
        )
        if gids:
            entries.append(_tc_entry(tc, gids, tp_by_id))
        else:
            pending_kind = (
                "candidate_or_ambiguous_trace"
                if any(
                    (tp := tp_by_id.get(pid)) is not None and set(tp.item_ids) & undetermined_items
                    for pid in tc.test_point_ids
                )
                else "no_reliable_trace"
            )
            report.judgments.append(
                TcJudgment(
                    tc_id=tc.id,
                    display_id=tc.display_id,
                    verdict=SemanticVerdict.PENDING_REVIEW,
                    reason="未送入 LLM：无可靠 AUTO_HIT Gold trace",
                    provenance=MetricProvenance.CODE,
                    pending_reason=pending_kind,
                )
            )
            report.pending_review.append(
                PendingReviewItem(pending_kind, tc.id, f"TestCase {tc.display_id} 无可靠 Gold trace，不送 LLM")
            )
    entries.sort(key=lambda e: e["display_id"])

    if client is None:
        for e in entries:
            report.judgments.append(
                TcJudgment(
                    tc_id=e["id"],
                    display_id=e["display_id"],
                    gold_ids=e["trace"]["gold_ids"],
                    verdict=SemanticVerdict.PENDING_REVIEW,
                    reason="client=None：不猜测语义分，全部降级待人工复核",
                    provenance=MetricProvenance.CODE,
                    pending_reason="no_client",
                )
            )
        _finalize(report, hard_report)
        return report

    # ---- TC 批 ----
    add_pool = [p for p in hard_report.pending_review if p.kind in ("unsupported_item", "unverified_output")]
    pool_refs = {p.ref_id for p in add_pool}
    for idx, chunk in enumerate(_make_batches(entries, gold)):
        meta = BatchMeta(index=idx, kind="tc_batch", n_cases=len(chunk), refs=sorted(e["display_id"] for e in chunk))
        by_disp = {e["display_id"]: e for e in chunk}
        raw: str | None = None
        data: object = None
        try:
            raw = client.chat(SEMANTIC_EVAL_PROMPT, _batch_payload(chunk, gold))
            meta.llm_calls = 1
            report.llm_calls += 1
            data = extract_json(raw)
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise ValueError("响应缺少 results 数组或 JSON 结构非法")
        except Exception as e:  # noqa: BLE001 - 批级失败隔离
            meta.ok = False
            meta.error = f"{type(e).__name__}: {e}"
            meta.failure_kind = "transport" if raw is None else "protocol"
            meta.evidence = _batch_evidence(raw, data, unreturned=len(chunk))
            report.failures.append(
                f"batch[{idx}] 失败（{meta.failure_kind}），该批 {len(chunk)} 条全部降级 PENDING_REVIEW: {meta.error}"
            )
            for ee in chunk:
                report.judgments.append(
                    TcJudgment(
                        tc_id=ee["id"],
                        display_id=ee["display_id"],
                        gold_ids=ee["trace"]["gold_ids"],
                        verdict=SemanticVerdict.PENDING_REVIEW,
                        reason="batch 调用/解析失败",
                        batch_index=idx,
                        provenance=MetricProvenance.CODE,
                        pending_reason="batch_failure",
                    )
                )
            report.batches.append(meta)
            continue

        returned: set[str] = set()
        fail_before = len(report.failures)
        for raw_j in data["results"]:
            if not isinstance(raw_j, dict):
                report.failures.append(f"batch[{idx}] 丢弃非 dict result 条目")
                continue
            disp = str(raw_j.get("display_id") or "")
            ee = by_disp.get(disp)
            if ee is None:
                report.failures.append(f"batch[{idx}] 丢弃越界 display_id={disp}（不在本批清单）")
                continue
            verdict_raw = str(raw_j.get("verdict") or "").strip().lower()
            if verdict_raw not in _VERDICT_MAP:
                report.failures.append(f"batch[{idx}] 丢弃越界 verdict={verdict_raw} (display_id={disp})")
                continue
            conf = _parse_confidence(raw_j.get("confidence"))
            kept, dropped = wl.validate_refs(raw_j.get("evidence_refs"))
            if dropped:
                report.failures.append(f"batch[{idx}] {disp} 丢弃非法 evidence_refs={dropped}")
            reason = str(raw_j.get("reason") or "").strip()

            fp_id = raw_j.get("forbidden_pattern_id")
            fp_field: str | None = None
            if fp_id not in (None, "", "null"):
                fp_id = str(fp_id)
                if fp_id in wl.pattern_ids:
                    fp_field = fp_id
                    report.forbidden_suspects.append(
                        ForbiddenSuspect(
                            pattern_id=fp_id, tc_id=ee["id"], display_id=disp, reason=reason, confidence=conf
                        )
                    )
                    report.pending_review.append(
                        PendingReviewItem(
                            "forbidden_suspect",
                            ee["id"],
                            f"疑似命中 {fp_id}（display_id={disp}）：仅嫌疑登记，人工终审",
                        )
                    )
                else:
                    report.failures.append(f"batch[{idx}] {disp} 丢弃非法 forbidden_pattern_id={fp_id}")

            verdict = SemanticVerdict.PENDING_REVIEW
            pending_reason: str | None = None
            if verdict_raw == "uncertain":
                pending_reason = "uncertain"
            elif conf is None or conf < LOW_CONFIDENCE:
                pending_reason = "low_confidence"
            elif verdict_raw == "incorrect" and not kept:
                pending_reason = "insufficient_evidence"
            else:
                verdict = {
                    "correct": SemanticVerdict.CORRECT,
                    "partial": SemanticVerdict.PARTIAL,
                    "incorrect": SemanticVerdict.INCORRECT,
                }[verdict_raw]
            report.judgments.append(
                TcJudgment(
                    tc_id=ee["id"],
                    display_id=disp,
                    gold_ids=ee["trace"]["gold_ids"],
                    verdict=verdict,
                    confidence=conf,
                    reason=reason,
                    evidence_refs=kept,
                    forbidden_pattern_id=fp_field,
                    batch_index=idx,
                    provenance=MetricProvenance.LLM,
                    pending_reason=pending_reason,
                )
            )
            returned.add(disp)  # 仅"真正产出 judgment"才算返回；越界 verdict 落入 missing_result 兜底
        for ee in chunk:
            if ee["display_id"] not in returned:
                report.judgments.append(
                    TcJudgment(
                        tc_id=ee["id"],
                        display_id=ee["display_id"],
                        gold_ids=ee["trace"]["gold_ids"],
                        verdict=SemanticVerdict.PENDING_REVIEW,
                        reason="LLM 未返回该用例结果",
                        batch_index=idx,
                        provenance=MetricProvenance.CODE,
                        pending_reason="missing_result",
                    )
                )
        _merge_additions(report, data.get("additional_valid_candidates"), wl, f"batch[{idx}]", pool_refs)
        dropped = len(report.failures) - fail_before
        unreturned = len(chunk) - len(returned)
        meta.evidence = _batch_evidence(raw, data, dropped=dropped, unreturned=unreturned)
        meta.failure_kind = "partial" if (dropped or unreturned) else ""
        report.batches.append(meta)

    # ---- 裁决批（S3 登记的 Gold 外产物，仅产预审建议）----
    base = len(report.batches)
    for gi, group in enumerate(
        [add_pool[i : i + PENDING_BATCH_SIZE] for i in range(0, len(add_pool), PENDING_BATCH_SIZE)]
    ):
        idx = base + gi
        meta = BatchMeta(index=idx, kind="pending_batch", n_cases=len(group), refs=sorted(p.ref_id for p in group))
        raw = None
        data = None
        try:
            raw = client.chat(SEMANTIC_EVAL_PROMPT, _pending_payload(gold, group, arts))
            meta.llm_calls = 1
            report.llm_calls += 1
            data = extract_json(raw)
            if not isinstance(data, dict):
                raise ValueError("裁决批响应 JSON 结构非法")
        except Exception as e:  # noqa: BLE001
            meta.ok = False
            meta.error = f"{type(e).__name__}: {e}"
            meta.failure_kind = "transport" if raw is None else "protocol"
            meta.evidence = _batch_evidence(raw, data, unreturned=len(group))
            report.failures.append(f"pending_batch[{idx}] 失败（{meta.failure_kind}，不影响 TC 判定）: {meta.error}")
            report.batches.append(meta)
            continue
        if data.get("results"):
            report.failures.append(f"pending_batch[{idx}] 忽略越界 results 字段（裁决批不产 verdict）")
        _merge_additions(report, data.get("additional_valid_candidates"), wl, f"pending_batch[{idx}]", pool_refs)
        meta.evidence = _batch_evidence(raw, data, dropped=0)
        report.batches.append(meta)

    _finalize(report, hard_report)
    return report


def _merge_additions(report: SoftMetricsReport, raw_list: object, wl: _Evidence, ctx: str, pool_refs: set[str]) -> None:
    """additional_valid_candidates 解析：独立于 results；ref 必须命中真实 pending 对象；只产 suggested。"""
    if not isinstance(raw_list, list):
        return
    existing = {(c.target_type, c.ref_id) for c in report.additional_candidates}
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        tt = str(raw.get("target_type") or "")
        ref = str(raw.get("ref_id") or "")
        if tt not in _VALID_TARGET_TYPES or ref not in pool_refs:
            report.failures.append(f"{ctx} 丢弃非法 additional_candidate target_type={tt} ref_id={ref}")
            continue
        kept, dropped = wl.validate_refs(raw.get("evidence_refs"))
        if dropped:
            report.failures.append(f"{ctx} additional_candidate {ref} 丢弃非法 evidence={dropped}")
        key = (tt, ref)
        if key in existing:
            continue  # 去重：先到先得（批序确定性保证可重放）
        existing.add(key)
        report.additional_candidates.append(
            AdditionalValidCandidate(
                target_type=tt,
                ref_id=ref,
                display_id=(str(raw["display_id"]) if raw.get("display_id") else None),
                reason=str(raw.get("reason") or ""),
                confidence=_parse_confidence(raw.get("confidence")),
                evidence_refs=kept,
            )
        )


def _finalize(report: SoftMetricsReport, hard_report: HardMetricsReport) -> None:
    counts = {v.value: 0 for v in SemanticVerdict}
    for j in report.judgments:
        counts[j.verdict.value] += 1
    report.verdict_counts = counts
    num = counts[SemanticVerdict.CORRECT.value]
    den = num + counts[SemanticVerdict.PARTIAL.value] + counts[SemanticVerdict.INCORRECT.value]
    report.semantic_accuracy = MetricCell(
        numerator=num,
        denominator=den,
        value=round(num / den, 4) if den else None,
        provenance=MetricProvenance.LLM,
        detail=(
            f"PENDING_REVIEW 不进分子分母（{counts[SemanticVerdict.PENDING_REVIEW.value]} 条待人工）"
            if den
            else "无可判定用例（分母 0，不伪造 0/100）"
        ),
    )
    report.judgments.sort(key=lambda j: j.display_id)
    report.forbidden_suspects.sort(key=lambda s: (s.display_id, s.pattern_id))
    report.additional_candidates.sort(key=lambda c: (c.target_type, c.ref_id))
    report.pending_review.sort(key=lambda p: (p.kind, p.ref_id))  # 报告对象与 payload 顺序双一致


# ============================================================
# JSON 序列化契约（S6/S7 直接消费；round-trip 稳定）
# ============================================================


def _cell_to(c: MetricCell) -> dict:
    return {
        "value": c.value,
        "numerator": c.numerator,
        "denominator": c.denominator,
        "provenance": c.provenance.value,
        "detail": c.detail,
    }


def _cell_from(d: dict) -> MetricCell:
    return MetricCell(
        numerator=d["numerator"],
        denominator=d["denominator"],
        value=d["value"],
        provenance=MetricProvenance(d["provenance"]),
        detail=d["detail"],
    )


def _reviewer_to(r: ReviewerBasedSnapshot | None) -> dict | None:
    if r is None:
        return None
    return {
        "report_id": r.report_id,
        "overall_score": r.overall_score,
        "scores": r.scores,
        "obligation_coverage": r.obligation_coverage,
        "finding_count": r.finding_count,
        "findings_by_provenance": r.findings_by_provenance,
        "provenance": r.provenance.value,
    }


def _reviewer_from(d: dict | None) -> ReviewerBasedSnapshot | None:
    if d is None:
        return None
    return ReviewerBasedSnapshot(
        report_id=d["report_id"],
        overall_score=d["overall_score"],
        scores=d["scores"],
        obligation_coverage=d["obligation_coverage"],
        finding_count=d["finding_count"],
        findings_by_provenance=d["findings_by_provenance"],
        provenance=MetricProvenance(d["provenance"]),
    )


def to_payload(report: SoftMetricsReport) -> dict:
    """稳定 JSON-safe dict：enum→.value、None 显式保留、列表已排序（S6 runset 直接 json.dumps）。"""
    return {
        "s5_schema": S5_SCHEMA_VERSION,
        "case_id": report.case_id,
        "gold_version": report.gold_version,
        "run_id": report.run_id,
        "prompt_version": report.s5_prompt_version,
        "model_meta": report.model_meta,
        "gold_based": {
            "semantic_accuracy": _cell_to(report.semantic_accuracy),
            "verdict_counts": report.verdict_counts,
            "judgments": [
                {
                    "tc_id": j.tc_id,
                    "display_id": j.display_id,
                    "gold_ids": j.gold_ids,
                    "verdict": j.verdict.value,
                    "confidence": j.confidence,
                    "reason": j.reason,
                    "evidence_refs": j.evidence_refs,
                    "forbidden_pattern_id": j.forbidden_pattern_id,
                    "batch_index": j.batch_index,
                    "provenance": j.provenance.value,
                    "pending_reason": j.pending_reason,
                }
                for j in report.judgments
            ],
            "forbidden_suspects": [
                {
                    "pattern_id": s.pattern_id,
                    "tc_id": s.tc_id,
                    "display_id": s.display_id,
                    "reason": s.reason,
                    "confidence": s.confidence,
                    "provenance": s.provenance.value,
                }
                for s in report.forbidden_suspects
            ],
            "additional_valid_candidates": [
                {
                    "target_type": c.target_type,
                    "ref_id": c.ref_id,
                    "display_id": c.display_id,
                    "reason": c.reason,
                    "confidence": c.confidence,
                    "evidence_refs": c.evidence_refs,
                    "status": c.status,
                    "provenance": c.provenance.value,
                }
                for c in report.additional_candidates
            ],
            "pending_review": [
                {"kind": p.kind, "ref_id": p.ref_id, "detail": p.detail}
                for p in sorted(report.pending_review, key=lambda p: (p.kind, p.ref_id))
            ],
        },
        "reviewer_based": _reviewer_to(report.reviewer_based),
        "batches": [
            {
                "index": b.index,
                "kind": b.kind,
                "n_cases": b.n_cases,
                "ok": b.ok,
                "llm_calls": b.llm_calls,
                "error": b.error,
                "refs": b.refs,
                "failure_kind": b.failure_kind,
                "evidence": dict(b.evidence),
            }
            for b in report.batches
        ],
        "failures": list(report.failures),
        "llm_calls": report.llm_calls,
    }


def from_payload(payload: dict) -> SoftMetricsReport:
    """to_payload 的逆（S6/S7 读回 + 测试 round-trip 用）。"""
    gb = payload["gold_based"]
    report = SoftMetricsReport(
        case_id=payload["case_id"],
        gold_version=payload["gold_version"],
        run_id=payload["run_id"],
        s5_prompt_version=payload["prompt_version"],
        model_meta=payload["model_meta"],
        semantic_accuracy=_cell_from(gb["semantic_accuracy"]),
        verdict_counts=gb["verdict_counts"],
        judgments=[
            TcJudgment(
                tc_id=j["tc_id"],
                display_id=j["display_id"],
                gold_ids=j["gold_ids"],
                verdict=SemanticVerdict(j["verdict"]),
                confidence=j["confidence"],
                reason=j["reason"],
                evidence_refs=j["evidence_refs"],
                forbidden_pattern_id=j["forbidden_pattern_id"],
                batch_index=j["batch_index"],
                provenance=MetricProvenance(j["provenance"]),
                pending_reason=j["pending_reason"],
            )
            for j in gb["judgments"]
        ],
        forbidden_suspects=[
            ForbiddenSuspect(
                pattern_id=s["pattern_id"],
                tc_id=s["tc_id"],
                display_id=s["display_id"],
                reason=s["reason"],
                confidence=s["confidence"],
                provenance=MetricProvenance(s["provenance"]),
            )
            for s in gb["forbidden_suspects"]
        ],
        additional_candidates=[
            AdditionalValidCandidate(
                target_type=c["target_type"],
                ref_id=c["ref_id"],
                display_id=c["display_id"],
                reason=c["reason"],
                confidence=c["confidence"],
                evidence_refs=c["evidence_refs"],
                status=c["status"],
                provenance=MetricProvenance(c["provenance"]),
            )
            for c in gb["additional_valid_candidates"]
        ],
        pending_review=[PendingReviewItem(**p) for p in gb["pending_review"]],
        batches=[BatchMeta(**b) for b in payload["batches"]],
        failures=list(payload["failures"]),
        llm_calls=payload["llm_calls"],
        reviewer_based=_reviewer_from(payload["reviewer_based"]),
    )
    return report


__all__ = [
    "BATCH_SIZE",
    "LOW_CONFIDENCE",
    "MAX_PAYLOAD_CHARS",
    "MAX_SINGLE_TC_CHARS",
    "S5_SCHEMA_VERSION",
    "AdditionalValidCandidate",
    "BatchMeta",
    "ForbiddenSuspect",
    "SemanticVerdict",
    "SoftMetricsReport",
    "TcJudgment",
    "additional_pool_refs",
    "evaluate_soft_metrics",
    "from_payload",
    "to_payload",
]
