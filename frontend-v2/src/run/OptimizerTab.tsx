// ============================================================
// 优化页（Step 8 产品化）：呈现「发现重复 → 归档 → 保留」的叙事，
// 而不是一个 archived_cases 数字。
// 诚实边界：OptimizerResult 不持久化（Step 8 决策），后端返回其持久化效果
// = ARCHIVED 用例清单 + Review duplication findings；无逐对 diff 数据源。
// ============================================================

import { Card, CaseStatusBadge, EmptyState } from "../components/ui";
import { Link } from "react-router-dom";
import { useRunBundle } from "./RunBundle";

export function OptimizerTab() {
  const { optimizer, review, testCases, openCase } = useRunBundle();

  if (!optimizer || !optimizer.archived_cases || optimizer.archived_cases.length === 0) {
    const dupCount = (review?.findings || []).filter((f) => f.dimension === "duplication").length;
    return (
      <EmptyState
        text={
          dupCount
            ? `评审发现 ${dupCount} 条重复关系 finding，但没有用例被归档（可能均不满足归档条件）。`
            : "本运行没有去重优化记录（未发现重复或评审未执行）。"
        }
      />
    );
  }

  const archived = optimizer.archived_count ?? optimizer.archived_cases.length;
  const total = testCases.length;
  const active = total - archived;
  const dupFindings = (review?.findings || []).filter((f) => f.dimension === "duplication");
  const archivedIds = new Set(optimizer.archived_cases.map((c) => c.id));
  // 与归档用例相关的 duplication finding（任一端是被归档用例）
  const related = dupFindings.filter((f) => {
    const detail = (f.detail || {}) as Record<string, unknown>;
    const counterpart = typeof detail.counterpart_id === "string" ? detail.counterpart_id : null;
    return (!!f.target_id && archivedIds.has(f.target_id)) || (!!counterpart && archivedIds.has(counterpart));
  });

  return (
    <div>
      <Card title="去重优化结果（Step 8 Dedup Optimizer）">
        <div className="pipeline" style={{ marginBottom: 12 }}>
          <div className="pipe-step done">
            <span className="dot" />
            评审发现重复 {dupFindings.length || "-"} 条
          </div>
          <div className="pipe-step done">
            <span className="dot" />
            归档 {archived} 条
          </div>
          <div className="pipe-step done">
            <span className="dot" />
            保留有效 {active} 条
          </div>
        </div>
        <div className="muted small">
          归档规则（后端代码保证）：重复对 canonicalize 后按 Survivor 优先级（人工修改 &gt; 优化 &gt; LLM &gt; 策略 &gt;
          校验 &gt; 迁移 → P0&gt;P3 → 创建更早 → 编号更小）保留一条、归档一条；ARCHIVED 不物理删除、可审计。
        </div>
        {optimizer.note ? <div className="alert alert-info small">{optimizer.note}</div> : null}
      </Card>

      <Card title={`被归档的用例（${archived}）`} extra={<Link className="btn btn-sm btn-ghost" to="../test-cases">在用例列表中查看（筛选已归档）</Link>}>
        <table className="table">
          <thead>
            <tr>
              <th>编号</th>
              <th>标题</th>
              <th>状态</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {optimizer.archived_cases.map((c) => (
              <tr key={c.id} className="clickable" onClick={() => openCase(c.id)}>
                <td className="col-muted">{c.display_id}</td>
                <td>{c.title}</td>
                <td>
                  <CaseStatusBadge status={c.status} />
                </td>
                <td className="col-muted">查看 →</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {related.length ? (
        <Card title={`相关重复关系 finding（${related.length} 条涉及归档用例）`}>
          {related.slice(0, 60).map((f, i) => {
            const detail = (f.detail || {}) as Record<string, unknown>;
            return (
              <div key={i} className="finding">
                <div className="f-head">
                  <span className="f-dim">重复（{String(detail.duplicate_level || "-")}）</span>
                  {typeof detail.similarity === "number" ? <span className="f-sev">相似度 {(detail.similarity * 100).toFixed(1)}%</span> : null}
                </div>
                <div className="f-issue">{f.issue}</div>
                <div className="f-link">
                  <button className="btn btn-sm btn-ghost" onClick={() => f.target_id && openCase(f.target_id)}>
                    查看用例 →
                  </button>
                </div>
              </div>
            );
          })}
          <div className="muted small">说明：归档/保留的配对关系来自 Review duplication finding 的 detail（对方用例 id），逐字段 diff 无后端数据源，不提供。</div>
        </Card>
      ) : null}
    </div>
  );
}
