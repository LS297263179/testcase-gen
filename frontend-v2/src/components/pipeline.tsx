// ============================================================
// AI 测试设计 Pipeline 核心视觉模块（P0）
// 阶段状态严格由真实落库数据推导（counts / 产物存在性 / run.status）；
// 不显示耗时（DB 无该数据）、不虚构执行进度。
// ============================================================

import { Link } from "react-router-dom";
import type { RunBundle } from "../run/RunBundle";
import { STEP_LABEL } from "../utils";

export type StageState = "done" | "failed" | "current" | "pending";

export interface Stage {
  key: string;
  state: StageState;
  count?: number | null;
  sub?: string;
}

/** 阶段达成推导：只看已落库证据，不猜测 */
export function deriveStages(b: RunBundle): Stage[] {
  const c = b.run.counts || {};
  const failedStep = b.run.failed_step || "";
  const llmPoints = b.testPoints.filter((p) => (p.provenance || "").toUpperCase() === "LLM").length;
  const strategyPoints = b.testPoints.filter((p) => (p.provenance || "").toUpperCase() === "STRATEGY").length;
  const archived = b.optimizer?.archived_count ?? 0;
  const evidence: Record<string, { done: boolean; count?: number | null; sub?: string }> = {
    ir: { done: (c.items ?? 0) > 0, count: b.requirements?.item_count ?? c.items ?? 0, sub: "需求项" },
    testpoints: { done: (c.points ?? 0) > 0, count: llmPoints, sub: "LLM 设计测试点" },
    strategy: { done: (c.obligations ?? 0) > 0 || strategyPoints > 0, count: strategyPoints, sub: `策略补充 · ${c.obligations ?? 0} 义务` },
    testcases: { done: (c.cases ?? 0) > 0, count: b.testCases.length || c.cases || 0, sub: "测试用例" },
    review: { done: b.review != null, count: b.review?.findings?.length, sub: "评审发现" },
    optimizer: { done: b.run.status === "done" || archived > 0, count: archived, sub: "归档重复用例" },
  };
  const order = ["ir", "testpoints", "strategy", "testcases", "review", "optimizer"];
  const running = b.run.status !== "done" && b.run.status !== "failed";
  let hitFailed = false;
  let markedRunning = false;
  return order.map((key) => {
    const e = evidence[key];
    let state: StageState = "pending";
    if (failedStep === key) {
      hitFailed = true;
      state = "failed";
    } else if (e.done) {
      state = "done";
    } else if (hitFailed) {
      state = "pending";
    } else if (running && !markedRunning) {
      markedRunning = true;
      state = "current";
    }
    return { key, state, count: e.count, sub: e.sub };
  });
}

const STATE_MARK: Record<StageState, string> = { done: "✓", current: "进行中", failed: "✗", pending: "待处理" };

/**
 * Pipeline 模块。
 * - interactive：各阶段可点击跳到对应 Tab（真实数据存在时才跳）
 * - compact：报告页小尺寸
 */
export function Pipeline({ bundle, interactive = true, compact = false }: { bundle: RunBundle; interactive?: boolean; compact?: boolean }) {
  const stages = deriveStages(bundle);
  const base = `/runs/${bundle.run.id}`;
  const linkFor: Record<string, string> = {
    ir: `${base}/requirements`,
    testpoints: `${base}/test-points`,
    strategy: `${base}/test-points`,
    testcases: `${base}/test-cases`,
    review: `${base}/review`,
    optimizer: `${base}/optimizer`,
  };
  return (
    <div>
      <div className="flow">
        {stages.map((s) => {
          const inner = (
            <>
              <div className="st-head">
                <span className="dot" />
                <span className="st-name">{STEP_LABEL[s.key] || s.key}</span>
              </div>
              {!compact && s.count != null ? <div className="st-count">{s.count}</div> : null}
              {!compact && s.sub ? <div className="st-sub">{s.sub}</div> : null}
              {compact ? (
                <div className="st-sub">
                  {s.count != null ? `${s.count} · ` : ""}
                  {STATE_MARK[s.state]}
                </div>
              ) : null}
            </>
          );
          const cls = `flow-stage ${s.state}`;
          return interactive && s.state === "done" ? (
            <Link key={s.key} to={linkFor[s.key]} className={cls} style={{ color: "inherit" }}>
              {inner}
            </Link>
          ) : (
            <div key={s.key} className={cls}>
              {inner}
            </div>
          );
        })}
      </div>
      {!compact ? (
        <div className="flow-note">
          阶段状态与数量来自真实落库产物（Step 2→8 自动链）；数据库不记录分阶段耗时，故不展示耗时。人工编辑 / 重新评审属于阶段 B（用户主动操作）。
        </div>
      ) : null}
    </div>
  );
}
