// ============================================================
// 工作台（P0 重排 + P1 问题摘要）
// 首屏 5 秒可识别：最新 Run / 当前质量 / 有无待处理问题 / 下一步动作。
// 数据 = 现有端点聚合（runs + 最新 run 的 review/coverage/test-cases），不新增后端能力。
// Findings 排序 critical → major → minor（同级保持原始顺序），最多展示 8 条。
// ============================================================

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getCoverage, getReview, listRuns, listTestCases } from "../api/v2";
import { EmptyState, LoadingBlock, RunStatusBadge, Section, SevTag, StatStrip } from "../components/ui";
import { Shell } from "../layout/Shell";
import type { CoverageDetail, ReviewFinding, ReviewReport, RunListItem } from "../types";
import { DIMENSION_LABEL, STEP_LABEL, timeAgo } from "../utils";

interface Latest {
  run: RunListItem;
  review: ReviewReport | null;
  coverage: CoverageDetail | null;
  pendingCases: number;
}

/** critical → major → minor → info，稳定排序（同级保持原始顺序） */
const SEV_RANK: Record<string, number> = { critical: 0, major: 1, minor: 2, info: 3 };
function sortFindings(list: ReviewFinding[]): ReviewFinding[] {
  return list
    .map((f, i) => ({ f, i }))
    .sort((a, b) => (SEV_RANK[(a.f.severity || "").toLowerCase()] ?? 9) - (SEV_RANK[(b.f.severity || "").toLowerCase()] ?? 9) || a.i - b.i)
    .map((x) => x.f);
}

export function Dashboard() {
  const [runs, setRuns] = useState<RunListItem[] | null>(null);
  const [latest, setLatest] = useState<Latest | null>(null);

  useEffect(() => {
    listRuns().then(async (items) => {
      setRuns(items);
      const first = items.find((i) => i.status === "done") || items[0];
      if (!first) return;
      const [rv, cov, tcs] = await Promise.allSettled([getReview(first.run_id), getCoverage(first.run_id), listTestCases(first.run_id)]);
      const ok = <T,>(s: PromiseSettledResult<T>) => (s.status === "fulfilled" ? s.value : null);
      const report = ok(rv);
      const cases = ok(tcs) || [];
      const pending = cases.filter((c) => ["edited", "re_review_required", "validation_failed"].includes(c.status || "")).length;
      setLatest({ run: first, review: report, coverage: ok(cov), pendingCases: pending });
    });
  }, []);

  const running = runs?.filter((r) => r.status !== "done" && r.status !== "failed") ?? [];
  const recent = (runs ?? []).slice(0, 5);

  // P1 聚合：待办 + findings 优先列表（duplication 归优化器处理，单独一行说明）
  const findings = latest?.review?.findings || [];
  const attention = sortFindings(findings.filter((f) => f.dimension !== "duplication")).slice(0, 8);
  const dupCount = findings.filter((f) => f.dimension === "duplication").length;

  return (
    <Shell crumbs={<b>工作台</b>}>
      <div className="page">
        <div className="page-header">
          <div className="ph-main">
            <h2>工作台</h2>
            <div className="ph-desc">需求 → AI 分析 → 测试设计 → AI 评审 → 优化 → 人工确认 → 报告</div>
          </div>
          <div className="ph-actions">
            <Link className="btn btn-primary" to="/runs/new">
              ＋ 新建测试运行
            </Link>
          </div>
        </div>

        {/* 顶部状态条：最新 Run / 质量 / 待处理 / 下一步 */}
        {latest ? (
          <div className={`run-banner ${latest.run.status === "done" ? "ok" : latest.run.status === "failed" ? "failed" : "running"}`} style={{ marginBottom: 24 }}>
            <div className="rb-main">
              <div className="rb-title">
                最新测试运行：<Link to={`/runs/${latest.run.run_id}`}>{latest.run.title}</Link>{" "}
                <RunStatusBadge status={latest.run.status} />
              </div>
              <div className="rb-desc">
                {latest.run.failed_step ? `失败于「${STEP_LABEL[latest.run.failed_step] || latest.run.failed_step}」` : `完成于 ${timeAgo(latest.run.created_at)}`}
                {latest.review ? ` · Overall ${latest.review.overall_score ?? "-"}（第 ${latest.review.revision} 轮）` : ""}
                {latest.pendingCases > 0 ? ` · ${latest.pendingCases} 条用例待人工处理` : ""}
              </div>
            </div>
            <div className="rb-actions">
              {latest.pendingCases > 0 ? (
                <Link className="btn btn-sm btn-primary" to={`/runs/${latest.run.run_id}/test-cases?status=pending`}>
                  处理待办
                </Link>
              ) : null}
              <Link className="btn btn-sm btn-outline" to={`/runs/${latest.run.run_id}`}>
                进入运行 →
              </Link>
            </div>
          </div>
        ) : runs !== null && !runs.length ? (
          <div className="run-banner" style={{ marginBottom: 24 }}>
            <div className="rb-main">
              <div className="rb-title">还没有测试运行</div>
              <div className="rb-desc">从一份需求开始：AI 将自动完成解析需求 → 测试设计 → 用例合成 → 评审 → 去重优化。</div>
            </div>
            <div className="rb-actions">
              <Link className="btn btn-sm btn-primary" to="/runs/new">
                新建第一次 AI 测试设计
              </Link>
            </div>
          </div>
        ) : null}

        {running.length > 0 ? (
          <Section title="进行中的运行" hint="Web 执行为同步模式：需保持发起页面打开">
            {running.map((r) => (
              <div key={r.run_id} className="done-row">
                <RunStatusBadge status={r.status} />
                <span className="d-text">
                  <Link to={`/runs/${r.run_id}`}>{r.title}</Link>
                </span>
                <span className="t-aux">{timeAgo(r.created_at)}</span>
              </div>
            ))}
          </Section>
        ) : null}

        <div className="split-21">
          {/* 主列：最近需要处理的问题（P1） */}
          <Section
            title="最近需要处理的问题"
            hint={latest ? `来自最新运行 · ${latest.run.title}` : undefined}
            actions={
              latest ? (
                <Link className="btn btn-sm btn-ghost" to={`/runs/${latest.run.run_id}/review`}>
                  全部评审 →
                </Link>
              ) : null
            }
          >
            {latest === undefined || (runs && !runs.length) ? (
              <EmptyState text="暂无数据。完成一次测试运行后，AI 评审发现的问题会汇总到这里。" />
            ) : !latest ? (
              <LoadingBlock text="正在汇总最新运行的质量问题..." />
            ) : (
              <>
                {latest.pendingCases > 0 ? (
                  <div className="alert alert-warn">
                    <b>{latest.pendingCases} 条用例待人工处理</b>（已编辑 / 待重新评审 / 校验失败）—{" "}
                    <Link to={`/runs/${latest.run.run_id}/test-cases?status=pending`}>去处理 →</Link>
                  </div>
                ) : null}
                {attention.length ? (
                  <div className="done-list">
                    {attention.map((f, i) => (
                      <div key={i} className="done-row">
                        <SevTag severity={f.severity} />
                        <span className="d-text">
                          <b>{DIMENSION_LABEL[f.dimension] || f.dimension}</b> · {f.issue.length > 64 ? f.issue.slice(0, 64) + "..." : f.issue}
                        </span>
                        <span className="d-link">
                          <Link className="btn btn-sm btn-ghost" to={`/runs/${latest.run.run_id}/review`}>
                            查看 →
                          </Link>
                        </span>
                      </div>
                    ))}
                  </div>
                ) : latest.review ? (
                  <div className="alert alert-info">最新运行的 AI 评审未发现需要人工处理的问题（重复类由去重优化器自动处理）。</div>
                ) : (
                  <div className="t-aux">最新运行尚无评审报告。</div>
                )}
                {dupCount > 0 ? (
                  <div className="t-aux" style={{ marginTop: 10 }}>
                    {dupCount} 条重复类 finding 已由去重优化器自动处理，<Link to={`/runs/${latest.run.run_id}/optimizer`}>查看归档结果 →</Link>
                  </div>
                ) : null}
              </>
            )}
          </Section>

          {/* 副列：质量概览 + 最近运行 */}
          <div>
            <Section title="当前质量" hint="最新运行">
              {latest?.review ? (
                <>
                  <StatStrip
                    items={[
                      { label: "Overall", value: latest.review.overall_score ?? "-", tone: "hl" },
                      {
                        label: "Obligation 覆盖",
                        value: latest.coverage?.strategy_obligation_coverage != null ? `${Math.round(latest.coverage.strategy_obligation_coverage * 100)}%` : "-",
                        tone: "ok",
                      },
                      {
                        label: "Item 覆盖",
                        value: latest.coverage?.requirement_item_coverage != null ? `${Math.round(latest.coverage.requirement_item_coverage * 100)}%` : "-",
                        tone: "ok",
                      },
                    ]}
                  />
                  <div style={{ marginTop: 12 }}>
                    {Object.entries(latest.review.scores || {}).map(([k, v]) => (
                      <div key={k} className="done-row" style={{ padding: "6px 2px" }}>
                        <span className="d-text">{DIMENSION_LABEL[k] || k}</span>
                        <b className="num">{v}</b>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <div className="t-aux">{runs === null ? "加载中..." : "暂无评审数据。"}</div>
              )}
            </Section>

            <Section
              title="最近测试运行"
              actions={
                <Link className="btn btn-sm btn-ghost" to="/runs">
                  全部 →
                </Link>
              }
            >
              {runs === null ? (
                <LoadingBlock />
              ) : recent.length ? (
                <div className="done-list">
                  {recent.map((r) => (
                    <div key={r.run_id} className="done-row">
                      <RunStatusBadge status={r.status} />
                      <span className="d-text">
                        <Link to={`/runs/${r.run_id}`}>{r.title}</Link>
                      </span>
                      <span className="t-aux">{r.failed_step ? `失败于 ${STEP_LABEL[r.failed_step] || r.failed_step}` : timeAgo(r.created_at)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState text="还没有测试运行。" />
              )}
            </Section>

            <div className="panel">
              <div className="t-section" style={{ marginBottom: 6 }}>AI 测试工作流</div>
              <div className="t-aux">
                需求 → <span className="ai-chip">AI</span> 分析 → 测试点（LLM + 策略引擎）→ 测试用例 → <span className="ai-chip">AI</span> 评审 → 去重优化 → 人工确认 →
                报告。列表为本人最近 50 条运行（MVP 行为）。
              </div>
            </div>
          </div>
        </div>
      </div>
    </Shell>
  );
}
