// ============================================================
// 运行概览（P0 重排 + P2）
// 首屏 5 秒可识别：Run 状态 / Pipeline 完成情况 / AI 主要产出 / 质量问题 / 下一步入口。
// 状态横幅严格基于真实 run.status；失败显示真实 failed_step + error_message，不伪造。
// 「AI 本次测试设计完成了什么」全部来自 RunBundle 已拉取的真实数据，零新增请求。
// ============================================================

import { Link } from "react-router-dom";
import { Pipeline } from "../components/pipeline";
import { AIChip, CoverageBar, ErrorAlert, Panel, RunStatusBadge, Section, StatStrip } from "../components/ui";
import { useRunBundle } from "./RunBundle";
import { DIMENSION_LABEL, STEP_LABEL } from "../utils";

export function RunOverview() {
  const b = useRunBundle();
  const { run, testPoints, testCases, review, coverage, requirements, optimizer } = b;
  const c = run.counts || {};

  const llmPoints = testPoints.filter((p) => (p.provenance || "").toUpperCase() === "LLM").length;
  const strategyPoints = testPoints.filter((p) => (p.provenance || "").toUpperCase() === "STRATEGY").length;
  const archived = testCases.filter((t) => t.status === "archived").length;
  const activeCases = testCases.length - archived;
  const codeCases = testCases.filter((t) => (t.generation_mode || "").toLowerCase() === "code").length;
  const llmCases = testCases.length - codeCases;
  const pendingConfirm = testCases.filter((t) => ["edited", "re_review_required", "validation_failed"].includes(t.status || "")).length;
  const nonDupFindings = (review?.findings || []).filter((f) => f.dimension !== "duplication");
  const dupFindings = (review?.findings || []).filter((f) => f.dimension === "duplication");
  const base = `/runs/${run.id}`;

  return (
    <div>
      {/* 状态横幅：真实 run.status */}
      <div className={`run-banner ${run.status === "done" ? "ok" : run.status === "failed" ? "failed" : "running"}`}>
        <div className="rb-icon">{run.status === "done" ? "✓" : run.status === "failed" ? "✗" : "⏳"}</div>
        <div className="rb-main">
          <div className="rb-title">
            {run.status === "done" ? "AI 测试设计已完成" : run.status === "failed" ? "运行失败" : "运行进行中（同步执行）"} <RunStatusBadge status={run.status} />
          </div>
          <div className="rb-desc">
            {run.status === "failed"
              ? `失败于「${STEP_LABEL[run.failed_step || ""] || run.failed_step || "未知阶段"}」：${run.error_message || "无错误详情"}`
              : run.status === "done"
                ? `产出 ${testCases.length || c.cases || 0} 条测试用例${review ? ` · Overall ${review.overall_score ?? "-"}` : ""}${pendingConfirm ? ` · ${pendingConfirm} 条待人工处理` : ""}`
                : "Step 2→8 自动链执行中，完成后可在本页查看结果"}
          </div>
        </div>
        <div className="rb-actions">
          {pendingConfirm > 0 ? (
            <Link className="btn btn-sm btn-primary" to={`${base}/test-cases?status=pending`}>
              处理 {pendingConfirm} 条待办
            </Link>
          ) : null}
          <Link className="btn btn-sm btn-outline" to={`${base}/report`}>
            查看报告
          </Link>
        </div>
      </div>
      {run.status === "failed" ? (
        <ErrorAlert error={new Error("可在「新建测试运行」重新发起；失败阶段与原因见上方横幅。")} />
      ) : null}

      {/* Pipeline 核心视觉模块 */}
      <Section title="AI 测试设计流水线" hint="点击已完成阶段可跳转对应页面">
        <Pipeline bundle={b} />
      </Section>

      {/* 主要产出统计条 */}
      <StatStrip
        items={[
          { label: "需求项", value: requirements?.item_count ?? c.items ?? 0, sub: "AI 解析" },
          { label: "测试点", value: testPoints.length || c.points || 0, sub: `LLM ${llmPoints} · 策略 ${strategyPoints}`, tone: "hl" },
          { label: "测试用例", value: testCases.length || c.cases || 0, sub: `有效 ${activeCases} · 归档 ${archived}`, tone: "hl" },
          { label: "覆盖义务", value: c.obligations ?? 0, sub: "策略兜底" },
          { label: "评审发现", value: review?.findings?.length ?? "-", sub: nonDupFindings.length ? `待处理 ${nonDupFindings.length}` : undefined, tone: nonDupFindings.length ? "warn" : "default" },
        ]}
      />

      <div className="split-21" style={{ marginTop: 28 }}>
        {/* AI 本次完成了什么（真实数据叙事） */}
        <Section title="AI 本次测试设计完成了什么" hint="全部数字来自真实落库产物">
          <div className="done-list">
            <DoneRow
              n={1}
              chip
              text={
                <>
                  <b>{requirements?.item_count ?? c.items ?? 0}</b> 个需求项被解析
                  {requirements ? <>（字段 {requirements.field_count} · 规则 {requirements.rule_count} · 权限 {requirements.permission_count}）</> : null}
                </>
              }
              to={`${base}/requirements`}
              linkText="查看需求"
            />
            <DoneRow
              n={2}
              chip
              text={
                <>
                  <b>{llmPoints}</b> 个测试点由 LLM 语义设计（单需求项 + 跨项联动）
                </>
              }
              to={`${base}/test-points`}
              linkText="查看测试点"
            />
            <DoneRow
              n={3}
              text={
                <>
                  策略引擎派生 <b>{c.obligations ?? 0}</b> 个覆盖义务，代码补充 <b>{strategyPoints}</b> 个边界/等价类/权限测试点（确定性兜底）
                </>
              }
              to={`${base}/test-points`}
              linkText="查看"
            />
            <DoneRow
              n={4}
              chip
              text={
                <>
                  合成 <b>{testCases.length || c.cases || 0}</b> 条测试用例（LLM 合成 {llmCases} · 代码模板 {codeCases}，1:1 追溯测试点）
                </>
              }
              to={`${base}/test-cases`}
              linkText="查看用例"
            />
            {review ? (
              <DoneRow
                n={5}
                chip
                text={
                  <>
                    AI 评审 6 维打分 Overall <b>{review.overall_score ?? "-"}</b>，发现 <b>{review.findings?.length ?? 0}</b> 条问题（准确性/可执行性/遗漏风险等{" "}
                    {nonDupFindings.length} 条需关注）
                  </>
                }
                to={`${base}/review`}
                linkText="查看质量问题"
              />
            ) : null}
            {optimizer && (optimizer.archived_count ?? 0) > 0 ? (
              <DoneRow
                n={6}
                text={
                  <>
                    去重优化器消费 <b>{dupFindings.length}</b> 条重复关系，自动归档 <b>{optimizer.archived_count}</b> 条重复用例，保留有效 {activeCases} 条
                  </>
                }
                to={`${base}/optimizer`}
                linkText="查看归档"
              />
            ) : null}
            {pendingConfirm > 0 ? (
              <DoneRow
                n={7}
                text={
                  <>
                    人工介入（阶段 B）：<b>{pendingConfirm}</b> 条用例待确认 / 重新评审
                  </>
                }
                to={`${base}/test-cases?status=pending`}
                linkText="去处理"
              />
            ) : null}
          </div>
        </Section>

        {/* 质量与覆盖（次级 panel） */}
        <div>
          <Section title="质量与覆盖">
            <Panel>
              {review ? (
                <>
                  <div style={{ marginBottom: 10 }}>
                    Overall <span className="t-num" style={{ color: "var(--primary)" }}>{review.overall_score ?? "-"}</span>
                    <span className="t-aux"> 第 {review.revision} 轮 · 触发 {review.trigger_type}</span>
                  </div>
                  <div className="score-grid">
                    {Object.entries(review.scores || {}).map(([k, v]) => (
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
                </>
              ) : (
                <div className="t-aux">尚无评审报告（评审阶段未完成或未启用评审模型）。</div>
              )}
              {coverage ? (
                <div className="score-grid" style={{ marginTop: 14 }}>
                  <CoverageBar label="Strategy Obligation" value={coverage.strategy_obligation_coverage} />
                  <CoverageBar label="Requirement Item" value={coverage.requirement_item_coverage} />
                </div>
              ) : null}
            </Panel>
          </Section>
          <Section title="下一步">
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {nonDupFindings.length > 0 ? (
                <Link className="btn btn-sm btn-primary" to={`${base}/review`}>
                  优先处理 {nonDupFindings.length} 个质量问题
                </Link>
              ) : null}
              {pendingConfirm > 0 ? (
                <Link className="btn btn-sm btn-outline" to={`${base}/test-cases?status=pending`}>
                  确认 {pendingConfirm} 条待重审用例
                </Link>
              ) : null}
              <Link className="btn btn-sm btn-outline" to={`${base}/test-cases`}>
                查看测试用例
              </Link>
              <Link className="btn btn-sm btn-ghost" to={`${base}/report`}>
                查看报告
              </Link>
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}

function DoneRow({ n, chip, text, to, linkText }: { n: number; chip?: boolean; text: React.ReactNode; to: string; linkText: string }) {
  return (
    <div className="done-row">
      <span className="d-idx">{n}</span>
      {chip ? <AIChip /> : <span style={{ width: 34, display: "inline-block", flexShrink: 0 }} />}
      <span className="d-text">{text}</span>
      <span className="d-link">
        <Link className="btn btn-sm btn-ghost" to={to}>
          {linkText} →
        </Link>
      </span>
    </div>
  );
}
