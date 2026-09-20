// ============================================================
// 报告页（P0 重排）：运行的一页纸总结，数据全部来自 RunBundle
// 已拉取的真实端点，不新增请求；打印友好。
// ============================================================

import { Link } from "react-router-dom";
import { Pipeline } from "../components/pipeline";
import { RunStatusBadge, Section, StatStrip } from "../components/ui";
import { useRunBundle } from "./RunBundle";
import { DIMENSION_LABEL } from "../utils";

export function ReportTab() {
  const b = useRunBundle();
  const { run, title, requirements, testPoints, testCases, review, coverage, optimizer } = b;
  const c = run.counts || {};
  const llm = testPoints.filter((p) => (p.provenance || "").toUpperCase() === "LLM").length;
  const strategy = testPoints.filter((p) => (p.provenance || "").toUpperCase() === "STRATEGY").length;
  const archived = testCases.filter((t) => t.status === "archived").length;
  const humanTouched = testCases.filter((t) => ["edited", "re_review_required", "confirmed"].includes(t.status || ""));
  const autoFixable = (review?.findings || []).filter((f) => f.auto_fixable).length;

  return (
    <div className="report-page">
      <Section title="1. 运行概要">
        <div className="kv-grid" style={{ marginBottom: 14 }}>
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
            <span className="v num">{run.id}</span>
          </div>
        </div>
        <Pipeline bundle={b} interactive={false} compact />
      </Section>

      <Section title="2. AI 分析产出（Step 2~5）">
        <StatStrip
          items={[
            { label: "需求项", value: requirements?.item_count ?? c.items ?? 0, sub: `字段 ${requirements?.field_count ?? "-"} · 规则 ${requirements?.rule_count ?? "-"} · 权限 ${requirements?.permission_count ?? "-"}` },
            { label: "测试点", value: testPoints.length, sub: `LLM ${llm} + 策略 ${strategy}`, tone: "hl" },
            { label: "覆盖义务", value: c.obligations ?? 0 },
            { label: "测试用例", value: testCases.length, sub: `有效 ${testCases.length - archived} / 归档 ${archived}`, tone: "hl" },
            {
              label: "覆盖率",
              value: coverage ? `${Math.round((coverage.strategy_obligation_coverage ?? 0) * 100)}% / ${coverage.requirement_item_coverage != null ? Math.round(coverage.requirement_item_coverage * 100) : "-"}%` : "-",
              sub: "义务/需求项",
            },
          ]}
        />
      </Section>

      <Section title="3. 质量结果（Step 7 AI 评审）">
        {review ? (
          <>
            <div style={{ marginBottom: 8 }}>
              Overall <span className="t-num">{review.overall_score ?? "-"}</span>
              <span className="t-aux"> （第 {review.revision} 轮 · 触发 {review.trigger_type}）</span>
            </div>
            <div className="kv-grid">
              {Object.entries(review.scores || {}).map(([k, v]) => (
                <div key={k} className="kv">
                  <span className="k">{DIMENSION_LABEL[k] || k}</span>
                  <span className="v num">{v}</span>
                </div>
              ))}
            </div>
            <div className="t-aux" style={{ marginTop: 8 }}>
              findings 共 {review.findings?.length ?? 0} 条，其中 auto_fixable {autoFixable} 条（重复类已由去重优化器消费）。
            </div>
          </>
        ) : (
          <div className="t-aux">无评审报告。</div>
        )}
      </Section>

      <Section title="4. 优化与人工活动（Step 8~9）">
        <div className="kv-grid">
          <div className="kv">
            <span className="k">去重归档</span>
            <span className="v num">{optimizer?.archived_count ?? 0} 条</span>
          </div>
          <div className="kv">
            <span className="k">人工介入用例</span>
            <span className="v num">{humanTouched.length} 条</span>
          </div>
        </div>
      </Section>

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
