// ============================================================
// AI 评审中心（Step 7 产品化）：6 维分数 → 按维度分组的 findings → 处理动作。
// finding 不是日志：每条可跳转到问题用例 Drawer；coverage 双指标 / executability
// 加权明细按后端真实结构呈现。
// ============================================================

import { useMemo, useState } from "react";
import { Card, CoverageBar, EmptyState, ProvBadge, ScoreBar } from "../components/ui";
import { useRunBundle } from "./RunBundle";
import { DIMENSION_LABEL } from "../utils";
import type { ReviewFinding } from "../types";

const SEV_ORDER = ["critical", "major", "minor", "info"];

export function ReviewTab() {
  const { review, openCase } = useRunBundle();
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
  const attention = (k: string, v?: number) => v != null && v < 80 && k !== "coverage";

  const findings = (review.findings || []).filter((f) => !dim || f.dimension === dim);

  return (
    <div>
      <div className="grid-2">
        <Card
          title="总体质量"
          extra={
            <span className="muted small">
              第 {review.revision} 轮 · 触发 {review.trigger_type}
            </span>
          }
        >
          <div style={{ marginBottom: 10 }}>
            Overall <b style={{ fontSize: 26, color: "var(--primary-deep)" }}>{review.overall_score ?? "-"}</b>
          </div>
          <div className="score-grid">
            {Object.entries(scores).map(([k, v]) => (
              <ScoreBar key={k} label={DIMENSION_LABEL[k] || k} value={v} attention={attention(k, v)} />
            ))}
          </div>
          {ex ? (
            <div className="muted small">
              可执行性明细（加权而非平均）：结构 {ex.structural_score} × {ex.structural_weight ?? 0.4} + 语义 {ex.semantic_score} ×{" "}
              {ex.semantic_weight ?? 0.6}
            </div>
          ) : null}
          {review.summary ? <div className="alert alert-info">{review.summary}</div> : null}
        </Card>
        <Card title="覆盖率双指标（严格分离，不合成）">
          <div className="score-grid">
            <CoverageBar label="Strategy Obligation" value={review.coverage_detail?.strategy_obligation_coverage} />
            <CoverageBar label="Requirement Item" value={review.coverage_detail?.requirement_item_coverage} />
          </div>
          <div className="section-title">需要处理</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {[...byDim.entries()].map(([k, arr]) => (
              <button key={k} className={`chip${dim === k ? " active" : ""}`} onClick={() => setDim(dim === k ? "" : k)}>
                {DIMENSION_LABEL[k] || k} {arr.length}
              </button>
            ))}
          </div>
          {(review.coverage_detail?.uncovered_item_ids || []).length ? (
            <div className="alert alert-warn small">
              未被覆盖需求项 {review.coverage_detail!.uncovered_item_ids!.length} 个（详见「需求与 AI 分析」页）。
            </div>
          ) : null}
        </Card>
      </div>

      <Card title={`Findings（${findings.length}${dim ? " / 筛选后" : " / 全部"}）`}>
        {!findings.length ? <div className="empty">无 finding。</div> : null}
        {SEV_ORDER.map((sev) => {
          const group = findings.filter((f) => (f.severity || "").toLowerCase() === sev);
          if (!group.length) return null;
          return (
            <div key={sev}>
              <div className="section-title">
                {sev.toUpperCase()}（{group.length}）
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
                    <button className="btn btn-sm btn-ghost" onClick={() => f.target_id && openCase(f.target_id)}>
                      查看问题用例 →
                    </button>
                  </div>
                </div>
              ))}
            </div>
          );
        })}
        {findings.some((f) => !SEV_ORDER.includes((f.severity || "").toLowerCase())) ? (
          <div>
            <div className="section-title">其他严重度</div>
            {findings
              .filter((f) => !SEV_ORDER.includes((f.severity || "").toLowerCase()))
              .map((f, i) => (
                <div key={i} className="finding">
                  <div className="f-head">
                    <span className="f-dim">{f.dimension}</span>
                    <span className="f-sev">{f.severity}</span>
                  </div>
                  <div className="f-issue">{f.issue}</div>
                </div>
              ))}
          </div>
        ) : null}
      </Card>

      {review.dimension_reasons && Object.keys(review.dimension_reasons).length ? (
        <Card title="维度评分理由（可解释评审）">
          {Object.entries(review.dimension_reasons).map(([k, v]) => (
            <div key={k} style={{ marginBottom: 6, fontSize: 12.5 }}>
              <b>{DIMENSION_LABEL[k] || k}：</b>
              {v}
            </div>
          ))}
        </Card>
      ) : null}
    </div>
  );
}
