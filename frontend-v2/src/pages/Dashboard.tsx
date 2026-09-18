// ============================================================
// 工作台：回答「最近发生了什么 / 现在有什么在跑 / 下一步做什么」。
// 数据 = GET /runs + 对最近运行拉 review/coverage（真实端点前端聚合）。
// ============================================================

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getCoverage, getReview, listRuns } from "../api/v2";
import { Card, EmptyState, LoadingBlock, MetricCard, RunStatusBadge } from "../components/ui";
import { Shell } from "../layout/Shell";
import type { CoverageDetail, ReviewReport, RunListItem } from "../types";
import { DIMENSION_LABEL, STEP_LABEL, timeAgo } from "../utils";

interface Latest {
  overall?: number;
  scores?: ReviewReport["scores"];
  coverage?: CoverageDetail | null;
}

export function Dashboard() {
  const [runs, setRuns] = useState<RunListItem[] | null>(null);
  const [latest, setLatest] = useState<Latest | null>(null);

  useEffect(() => {
    listRuns().then(async (items) => {
      setRuns(items);
      const first = items.find((i) => i.status === "done") || items[0];
      if (!first) return;
      const [rv, cov] = await Promise.allSettled([getReview(first.run_id), getCoverage(first.run_id)]);
      const ok = <T,>(s: PromiseSettledResult<T>) => (s.status === "fulfilled" ? s.value : null);
      const report = ok(rv);
      setLatest({ overall: report?.overall_score, scores: report?.scores, coverage: ok(cov) });
    });
  }, []);

  const running = runs?.filter((r) => r.status !== "done" && r.status !== "failed") ?? [];
  const recent = (runs ?? []).slice(0, 5);

  return (
    <Shell crumbs={<b>工作台</b>}>
      <div className="page">
        <div className="page-header">
          <div className="ph-main">
            <h2>工作台</h2>
            <div className="ph-desc">AI + 规则 + 人，共同完成测试设计：需求 → 测试点 → 用例 → 评审 → 优化 → 人工确认 → 报告。</div>
          </div>
          <div className="ph-actions">
            <Link className="btn btn-primary" to="/runs/new">
              ＋ 新建测试运行
            </Link>
          </div>
        </div>

        {running.length > 0 ? (
          <Card title="进行中的运行">
            {running.map((r) => (
              <div key={r.run_id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0" }}>
                <RunStatusBadge status={r.status} />
                <Link to={`/runs/${r.run_id}`}>{r.title}</Link>
                <span className="muted small">{timeAgo(r.created_at)}</span>
              </div>
            ))}
            <div className="muted small">Web 执行为同步模式：进行中的运行需保持其页面打开；此处展示的是已落库状态。</div>
          </Card>
        ) : null}

        <div className="grid-2">
          <Card
            title="最近测试运行"
            extra={
              <Link className="btn btn-sm btn-ghost" to="/runs">
                全部 →
              </Link>
            }
          >
            {runs === null ? (
              <LoadingBlock />
            ) : recent.length ? (
              recent.map((r) => (
                <div key={r.run_id} className="trace-card" style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <Link to={`/runs/${r.run_id}`} style={{ fontWeight: 600 }}>
                    {r.title}
                  </Link>
                  <RunStatusBadge status={r.status} />
                  <span className="muted small">
                    {r.failed_step ? `失败于 ${STEP_LABEL[r.failed_step] || r.failed_step}` : timeAgo(r.created_at)}
                  </span>
                </div>
              ))
            ) : (
              <EmptyState text="还没有测试运行。" action={<Link className="btn btn-sm btn-primary" to="/runs/new">新建第一次 AI 测试设计</Link>} />
            )}
          </Card>

          <Card
            title="最近一次质量概览"
            extra={
              latest?.overall != null ? (
                <span>
                  Overall <b style={{ color: "var(--primary-deep)", fontSize: 16 }}>{latest.overall}</b>
                </span>
              ) : null
            }
          >
            {latest?.scores ? (
              <div className="score-grid">
                {Object.entries(latest.scores).map(([k, v]) => (
                  <div key={k} className="score-item">
                    <div className="score-label">
                      <span>
                        {DIMENSION_LABEL[k] || k} <b>{v}</b>
                      </span>
                    </div>
                    <div className="bar">
                      <div className={`bar-fill ${(v ?? 0) >= 85 ? "good" : (v ?? 0) >= 60 ? "mid" : "low"}`} style={{ width: `${v ?? 0}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="muted small">暂无评审数据。</div>
            )}
            {latest?.coverage ? (
              <div className="muted small" style={{ marginTop: 8 }}>
                Obligation 覆盖 {((latest.coverage.strategy_obligation_coverage ?? 0) * 100).toFixed(0)}% · Item 覆盖{" "}
                {latest.coverage.requirement_item_coverage != null ? (latest.coverage.requirement_item_coverage * 100).toFixed(0) + "%" : "-"}
              </div>
            ) : null}
          </Card>
        </div>

        <div className="grid-4" style={{ marginTop: 4 }}>
          <MetricCard label="测试运行总数（最近 50）" value={runs?.length ?? "-"} sub="列表端点为 MVP 行为：本人最近 50 条" />
          <MetricCard label="产品主线" value={<span style={{ fontSize: 13, fontWeight: 500 }}>测试运行 → 需求 → 设计 → 评审 → 优化 → 确认 → 报告</span>} />
        </div>
      </div>
    </Shell>
  );
}
