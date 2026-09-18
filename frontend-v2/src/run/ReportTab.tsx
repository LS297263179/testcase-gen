// ============================================================
// 报告页：运行的一页纸总结（需求规模 → 设计产出 → 质量 → 优化 → 人工活动），
// 数据全部来自 RunBundle 已拉取的真实端点，不新增请求；打印友好。
// ============================================================

import { Link } from "react-router-dom";
import { Card, CoverageBar, RunStatusBadge } from "../components/ui";
import { Pipeline } from "./RunOverview";
import { useRunBundle } from "./RunBundle";
import { DIMENSION_LABEL } from "../utils";

export function ReportTab() {
  const { run, title, requirements, testPoints, testCases, review, coverage, optimizer } = useRunBundle();
  const c = run.counts || {};
  const llm = testPoints.filter((p) => (p.provenance || "").toUpperCase() === "LLM").length;
  const strategy = testPoints.filter((p) => (p.provenance || "").toUpperCase() === "STRATEGY").length;
  const archived = testCases.filter((t) => t.status === "archived").length;
  const humanEdited = testCases.filter((t) => ["edited", "re_review_required", "confirmed"].includes(t.status || ""));
  const autoFixable = (review?.findings || []).filter((f) => f.auto_fixable).length;

  return (
    <div className="report-page">
      <Card title="1. 运行概要">
        <div className="kv-grid">
          <div className="kv">
            <span className="k">需求</span>
            <span className="v">{title}</span>
          </div>
          <div className="kv">
            <span className="k">版本</span>
            <span className="v">v{requirements?.version?.version_no ?? "-"}</span>
          </div>
          <div className="kv">
            <span className="k">状态</span>
            <span className="v">
              <RunStatusBadge status={run.status} />
            </span>
          </div>
          <div className="kv">
            <span className="k">创建时间</span>
            <span className="v">{run.created_at || "-"}</span>
          </div>
          <div className="kv">
            <span className="k">Run ID</span>
            <span className="v">{run.id}</span>
          </div>
        </div>
        <hr className="divider" />
        <Pipeline />
      </Card>

      <Card title="2. AI 分析产出（Step 2~5）">
        <div className="kv-grid">
          <div className="kv">
            <span className="k">需求项</span>
            <span className="v">{requirements?.item_count ?? c.items ?? 0}</span>
          </div>
          <div className="kv">
            <span className="k">字段 / 规则 / 权限</span>
            <span className="v">
              {requirements?.field_count ?? 0} / {requirements?.rule_count ?? 0} / {requirements?.permission_count ?? 0}
            </span>
          </div>
          <div className="kv">
            <span className="k">测试点</span>
            <span className="v">
              {testPoints.length}（LLM {llm} + 策略 {strategy}）
            </span>
          </div>
          <div className="kv">
            <span className="k">覆盖义务</span>
            <span className="v">{c.obligations ?? 0}</span>
          </div>
          <div className="kv">
            <span className="k">测试用例</span>
            <span className="v">
              {testCases.length}（有效 {testCases.length - archived} / 归档 {archived}）
            </span>
          </div>
        </div>
        {coverage ? (
          <div className="score-grid" style={{ marginTop: 10 }}>
            <CoverageBar label="Strategy Obligation 覆盖" value={coverage.strategy_obligation_coverage} />
            <CoverageBar label="Requirement Item 覆盖" value={coverage.requirement_item_coverage} />
          </div>
        ) : null}
      </Card>

      <Card title="3. 质量结果（Step 7 AI 评审）">
        {review ? (
          <>
            <div style={{ marginBottom: 6 }}>
              Overall <b style={{ fontSize: 18 }}>{review.overall_score ?? "-"}</b>
              <span className="muted small">（第 {review.revision} 轮 · 触发 {review.trigger_type}）</span>
            </div>
            <div className="kv-grid">
              {Object.entries(review.scores || {}).map(([k, v]) => (
                <div key={k} className="kv">
                  <span className="k">{DIMENSION_LABEL[k] || k}</span>
                  <span className="v">{v}</span>
                </div>
              ))}
            </div>
            <div className="muted small" style={{ marginTop: 8 }}>
              findings 共 {review.findings?.length ?? 0} 条，其中 auto_fixable {autoFixable} 条（已由去重优化器消费重复类）。
            </div>
          </>
        ) : (
          <div className="muted">无评审报告。</div>
        )}
      </Card>

      <Card title="4. 优化与人工活动（Step 8~9）">
        <div className="kv-grid">
          <div className="kv">
            <span className="k">去重归档</span>
            <span className="v">{optimizer?.archived_count ?? 0} 条</span>
          </div>
          <div className="kv">
            <span className="k">人工介入用例</span>
            <span className="v">{humanEdited.length} 条</span>
          </div>
        </div>
      </Card>

      <div className="no-print" style={{ display: "flex", gap: 8 }}>
        <Link className="btn btn-outline" to={`/runs/${run.id}/test-cases`}>
          进入测试用例
        </Link>
        <Link className="btn btn-outline" to={`/runs/${run.id}/review`}>
          进入 AI 评审
        </Link>
      </div>
    </div>
  );
}
