// ============================================================
// 运行概览：AI 工作流的「我在哪 / 做了什么 / 下一步」主界面。
// Pipeline 步骤条只展示「阶段达成状态」——由 counts / review / status 真实数据推导，
// runs 表不存分阶段耗时，因此不显示耗时（诚实原则）。
// ============================================================

import { Link } from "react-router-dom";
import { useRunBundle } from "./RunBundle";
import { Card, CoverageBar, ErrorAlert, MetricCard, RunStatusBadge } from "../components/ui";
import { STEP_LABEL } from "../utils";
import type { RunBundle } from "./RunBundle";

interface StageState {
  key: string;
  state: "done" | "failed" | "running" | "pending";
}

/** 阶段达成推导：只看已落库证据，不猜测 */
export function deriveStages(b: RunBundle): StageState[] {
  const c = b.run.counts || {};
  const failedStep = b.run.failed_step || "";
  const evidence: Record<string, boolean> = {
    ir: (c.items ?? 0) > 0,
    testpoints: (c.points ?? 0) > 0,
    strategy: (c.obligations ?? 0) > 0 || (b.testPoints || []).some((p) => (p.provenance || "").toUpperCase() === "STRATEGY"),
    testcases: (c.cases ?? 0) > 0,
    review: b.review != null,
    optimizer: b.run.status === "done" || (b.optimizer?.archived_count ?? 0) > 0,
  };
  const order = ["ir", "testpoints", "strategy", "testcases", "review", "optimizer"];
  const running = b.run.status !== "done" && b.run.status !== "failed";
  let hitFailed = false;
  let markedRunning = false;
  return order.map((key) => {
    if (failedStep === key) {
      hitFailed = true;
      return { key, state: "failed" as const };
    }
    if (evidence[key]) return { key, state: "done" as const };
    if (hitFailed) return { key, state: "pending" as const };
    if (running && !markedRunning) {
      markedRunning = true;
      return { key, state: "running" as const };
    }
    return { key, state: "pending" as const };
  });
}

export function Pipeline() {
  const b = useRunBundle();
  const stages = deriveStages(b);
  return (
    <div>
      <div className="pipeline">
        {stages.map((s) => (
          <div key={s.key} className={`pipe-step ${s.state === "pending" ? "" : s.state === "running" ? "failed" : s.state}`}>
            <span className="dot" />
            {STEP_LABEL[s.key] || s.key}
            {s.state === "done" ? " ✓" : s.state === "failed" ? " ✗" : s.state === "running" ? " ⏳" : ""}
          </div>
        ))}
      </div>
      <div className="pipe-hint">
        阶段：需求解析 → 测试点生成 → 策略补充 → 用例生成 → AI 评审 → 去重优化（Step 2→8 自动链；人工编辑/重评审为用户主动操作 · 阶段 B）。
        数据库仅记录阶段产物计数与终态，不记录分阶段耗时。
      </div>
    </div>
  );
}

export function RunOverview() {
  const b = useRunBundle();
  const { run, testPoints, testCases, review, coverage, requirements } = b;
  const c = run.counts || {};
  const llmPoints = testPoints.filter((p) => (p.provenance || "").toUpperCase() === "LLM").length;
  const strategyPoints = testPoints.filter((p) => (p.provenance || "").toUpperCase() === "STRATEGY").length;
  const archived = testCases.filter((t) => t.status === "archived").length;
  const activeCases = testCases.length - archived;
  const pendingConfirm = testCases.filter((t) => t.status === "re_review_required" || t.status === "edited").length;
  const findingsNeedAttention = (review?.findings || []).filter((f) => f.dimension !== "duplication").length;

  return (
    <div>
      {run.status === "failed" ? (
        <ErrorAlert
          error={
            new Error(
              `运行失败于「${STEP_LABEL[run.failed_step || ""] || run.failed_step || "未知阶段"}」阶段：${run.error_message || "无错误详情"}`,
            )
          }
        />
      ) : null}

      <Card title="AI 工作流进度">
        <Pipeline />
      </Card>

      <div className="grid-4" style={{ marginBottom: 16 }}>
        <MetricCard label="需求项" value={c.items ?? requirements?.item_count ?? 0} sub={run.status === "ingesting" ? "解析中" : "RequirementItem"} />
        <MetricCard
          label="测试点"
          value={testPoints.length || c.points || 0}
          sub={`LLM ${llmPoints} · Strategy ${strategyPoints}`}
          accent
        />
        <MetricCard label="测试用例" value={testCases.length || c.cases || 0} sub={`有效 ${activeCases} · 归档 ${archived}`} accent />
        <MetricCard label="覆盖义务" value={c.obligations ?? 0} sub="Strategy Engine 派生" />
      </div>

      <div className="grid-2">
        <Card title="质量概览（最新评审）" extra={<RunStatusBadge status={run.status} />}>
          {review ? (
            <>
              <div style={{ marginBottom: 8 }}>
                Overall <b style={{ fontSize: 20, color: "var(--primary-deep)" }}>{review.overall_score ?? "-"}</b>
                <span className="muted small"> 第 {review.revision} 轮 · 触发 {review.trigger_type}</span>
              </div>
              <div className="score-grid">
                {Object.entries(review.scores || {}).map(([k, v]) => (
                  <div key={k} className="score-item">
                    <div className="score-label">
                      <span>
                        {k} <b>{v}</b>
                      </span>
                    </div>
                    <div className="bar">
                      <div className={`bar-fill ${(v ?? 0) >= 85 ? "good" : (v ?? 0) >= 60 ? "mid" : "low"}`} style={{ width: `${v ?? 0}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="muted small">尚无评审报告（评审阶段未完成或未启用评审模型）。</div>
          )}
        </Card>
        <Card title="覆盖率（双指标严格分离）">
          {coverage ? (
            <div className="score-grid">
              <CoverageBar label="Strategy Obligation" value={coverage.strategy_obligation_coverage} />
              <CoverageBar label="Requirement Item" value={coverage.requirement_item_coverage} />
            </div>
          ) : (
            <div className="muted small">无覆盖数据。</div>
          )}
          <hr className="divider" />
          <div className="section-title">下一步</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {findingsNeedAttention > 0 ? (
              <Link className="btn btn-sm btn-primary" to={`${`/runs/${run.id}/review`}`}>
                查看 {findingsNeedAttention} 个质量问题
              </Link>
            ) : null}
            {pendingConfirm > 0 ? (
              <Link className="btn btn-sm btn-outline" to={`/runs/${run.id}/test-cases`}>
                确认 {pendingConfirm} 条待重审用例
              </Link>
            ) : null}
            <Link className="btn btn-sm btn-outline" to={`/runs/${run.id}/test-cases`}>
              查看测试用例
            </Link>
            <Link className="btn btn-sm btn-ghost" to={`/runs/${run.id}/report`}>
              查看报告
            </Link>
          </div>
        </Card>
      </div>
    </div>
  );
}
