// ============================================================
// AI 评审中心（P0 重排 + P4 优先查看/建议处理）
// 纯前端聚合，不改 Reviewer 算法：
// - 「建议处理 · 优先查看」：critical → major → minor 稳定排序，排除 duplication
//   （重复类由 Step 8 优化器处理，单独一行说明并跳转）
// - 点击定位具体对象：testcase → 打开 Case Drawer；requirement_item → 跳需求页并锚点定位
// ============================================================

import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { EmptyState, Panel, ProvBadge, ScoreBar, Section, SevTag } from "../components/ui";
import { useRunBundle } from "./RunBundle";
import { DIMENSION_LABEL } from "../utils";
import type { ReviewFinding } from "../types";

const SEV_ORDER = ["critical", "major", "minor", "info"];
const SEV_RANK: Record<string, number> = { critical: 0, major: 1, minor: 2, info: 3 };

export function ReviewTab() {
  const { review, openCase, run } = useRunBundle();
  const nav = useNavigate();
  const [dim, setDim] = useState("");

  const byDim = useMemo(() => {
    const m = new Map<string, ReviewFinding[]>();
    (review?.findings || []).forEach((f) => {
      const key = f.dimension || "-";
      m.set(key, [...(m.get(key) || []), f]);
    });
    return m;
  }, [review]);

  if (!review) return <EmptyState text="该运行暂无评审报告（评审阶段未完成或未启用评审模型）。" />;

  const scores = review.scores || {};
  const ex = review.executability_detail;
  const all = review.findings || [];
  const dup = all.filter((f) => f.dimension === "duplication");
  const attention = all
    .filter((f) => f.dimension !== "duplication")
    .map((f, i) => ({ f, i }))
    .sort((a, b) => (SEV_RANK[(a.f.severity || "").toLowerCase()] ?? 9) - (SEV_RANK[(b.f.severity || "").toLowerCase()] ?? 9) || a.i - b.i)
    .map((x) => x.f);
  const urgent = attention.filter((f) => ["critical", "major"].includes((f.severity || "").toLowerCase())).length;

  /** P4：finding → 定位具体对象 */
  const locate = (f: ReviewFinding) => {
    if ((f.target_type || "testcase") === "testcase" && f.target_id) {
      openCase(f.target_id);
    } else if (f.target_id) {
      nav(`/runs/${run.id}/requirements`, { state: { focusItem: f.target_id } });
    }
  };
  const actionLabel = (f: ReviewFinding) => ((f.target_type || "testcase") === "testcase" ? "查看用例 →" : "查看需求 →");

  const findings = all.filter((f) => !dim || f.dimension === dim);

  return (
    <div>
      {/* 建议处理 · 优先查看 */}
      <Section title="建议处理 · 优先查看" hint={`需优先处理 ${urgent} 条 · 一般 ${attention.length - urgent} 条 · 重复 ${dup.length} 条已由优化器处理`}>
        {attention.length ? (
          <Panel>
            {attention.slice(0, 12).map((f, i) => (
              <div key={i} className="done-row">
                <SevTag severity={f.severity} />
                <span className="d-text">
                  <b>{DIMENSION_LABEL[f.dimension] || f.dimension}</b> · {f.issue.length > 72 ? f.issue.slice(0, 72) + "..." : f.issue}
                  {f.suggestion ? <span className="t-aux"> 建议：{f.suggestion.length > 40 ? f.suggestion.slice(0, 40) + "..." : f.suggestion}</span> : null}
                </span>
                <span className="d-link">
                  <button className="btn btn-sm btn-ghost" onClick={() => locate(f)}>
                    {actionLabel(f)}
                  </button>
                </span>
              </div>
            ))}
          </Panel>
        ) : (
          <div className="alert alert-info">AI 评审未发现需要人工处理的问题（重复类由去重优化器自动处理）。</div>
        )}
        {dup.length ? (
          <div className="t-aux" style={{ marginTop: 10 }}>
            {dup.length} 条重复类 finding（auto_fixable）已由去重优化器消费，<Link to={`/runs/${run.id}/optimizer`}>查看归档结果 →</Link>
          </div>
        ) : null}
      </Section>

      <div className="split-21">
        <Section title="六维评分" hint={`第 ${review.revision} 轮 · 触发 ${review.trigger_type}`}>
          <Panel>
            <div style={{ marginBottom: 10 }}>
              Overall <span className="t-num" style={{ color: "var(--primary)" }}>{review.overall_score ?? "-"}</span>
            </div>
            <div className="score-grid">
              {Object.entries(scores).map(([k, v]) => (
                <ScoreBar key={k} label={DIMENSION_LABEL[k] || k} value={v} attention={v != null && v < 80 && k !== "coverage"} />
              ))}
            </div>
            {ex ? (
              <div className="t-aux" style={{ marginTop: 10 }}>
                可执行性明细（加权而非平均）：结构 {ex.structural_score} × {ex.structural_weight ?? 0.4} + 语义 {ex.semantic_score} × {ex.semantic_weight ?? 0.6}
              </div>
            ) : null}
            {review.summary ? <div className="alert alert-info">{review.summary}</div> : null}
            <div className="score-grid" style={{ marginTop: 12 }}>
              {review.coverage_detail ? (
                <>
                  <div className="score-item">
                    <div className="score-label">
                      <span>
                        Strategy Obligation 覆盖 <b>{Math.round((review.coverage_detail.strategy_obligation_coverage ?? 0) * 100)}%</b>
                      </span>
                    </div>
                    <div className="bar">
                      <div className="bar-fill cov" style={{ width: `${(review.coverage_detail.strategy_obligation_coverage ?? 0) * 100}%` }} />
                    </div>
                  </div>
                  <div className="score-item">
                    <div className="score-label">
                      <span>
                        Requirement Item 覆盖 <b>{Math.round((review.coverage_detail.requirement_item_coverage ?? 0) * 100)}%</b>
                      </span>
                    </div>
                    <div className="bar">
                      <div className="bar-fill cov" style={{ width: `${(review.coverage_detail.requirement_item_coverage ?? 0) * 100}%` }} />
                    </div>
                  </div>
                </>
              ) : null}
            </div>
            {(review.coverage_detail?.uncovered_item_ids || []).length ? (
              <div className="alert alert-warn small">未被覆盖需求项 {review.coverage_detail!.uncovered_item_ids!.length} 个（详见「需求与 AI 分析」页）。</div>
            ) : null}
          </Panel>
        </Section>

        <Section
          title={`全部 Findings（${findings.length}${dim ? " / 筛选后" : " / 全部"}）`}
          actions={
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {[...byDim.entries()].map(([k, arr]) => (
                <button key={k} className={`chip${dim === k ? " active" : ""}`} onClick={() => setDim(dim === k ? "" : k)}>
                  {DIMENSION_LABEL[k] || k} {arr.length}
                </button>
              ))}
            </div>
          }
        >
          {!findings.length ? <div className="empty">无 finding。</div> : null}
          {SEV_ORDER.map((sev) => {
            const group = findings.filter((f) => (f.severity || "").toLowerCase() === sev);
            if (!group.length) return null;
            return (
              <div key={sev} style={{ marginBottom: 14 }}>
                <div className="t-aux" style={{ marginBottom: 6, fontWeight: 700, textTransform: "uppercase" }}>
                  {sev}（{group.length}）
                </div>
                {group.map((f, i) => (
                  <div key={i} className={`finding sev-${sev}`}>
                    <div className="f-head">
                      <span className="f-dim">{DIMENSION_LABEL[f.dimension] || f.dimension}</span>
                      <ProvBadge prov={f.provenance} />
                      {f.auto_fixable ? <span className="f-autofix">auto_fixable</span> : null}
                    </div>
                    <div className="f-issue">{f.issue}</div>
                    {f.suggestion ? <div className="f-sug">建议：{f.suggestion}</div> : null}
                    {f.detail ? <div className="f-sug">明细：{JSON.stringify(f.detail)}</div> : null}
                    <div className="f-link">
                      <button className="btn btn-sm btn-ghost" onClick={() => locate(f)}>
                        {actionLabel(f)}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            );
          })}
        </Section>
      </div>

      {review.dimension_reasons && Object.keys(review.dimension_reasons).length ? (
        <Section title="维度评分理由" hint="可解释评审：软维度来自 LLM，硬维度由代码给出">
          <Panel>
            {Object.entries(review.dimension_reasons).map(([k, v]) => (
              <div key={k} style={{ marginBottom: 6, fontSize: 12.5 }}>
                <b>{DIMENSION_LABEL[k] || k}：</b>
                {v}
              </div>
            ))}
          </Panel>
        </Section>
      ) : null}
    </div>
  );
}
