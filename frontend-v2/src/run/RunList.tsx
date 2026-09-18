// ============================================================
// 测试运行列表（核心业务对象入口）。MVP 后端约束：仅本人最近 50 条。
// ============================================================

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listRuns } from "../api/v2";
import { Card, EmptyState, ErrorAlert, LoadingBlock, RunStatusBadge } from "../components/ui";
import { Shell } from "../layout/Shell";
import type { RunListItem } from "../types";
import { STEP_LABEL, timeAgo } from "../utils";

export function RunList() {
  const [items, setItems] = useState<RunListItem[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = () => {
    setError(null);
    listRuns()
      .then(setItems)
      .catch(setError);
  };
  useEffect(load, []);

  return (
    <Shell crumbs={<b>测试运行</b>}>
      <div className="page">
        <div className="page-header">
          <div className="ph-main">
            <h2>测试运行</h2>
            <div className="ph-desc">一次运行 = 需求解析 → 测试点 → 策略 → 用例 → AI 评审 → 去重优化的完整自动链路（Step 2→8）。</div>
          </div>
          <div className="ph-actions">
            <Link className="btn btn-primary" to="/runs/new">
              ＋ 新建测试运行
            </Link>
            <button className="btn btn-outline btn-sm" onClick={load}>
              刷新
            </button>
          </div>
        </div>
        {error ? <ErrorAlert error={error} onRetry={load} /> : null}
        {items === null && !error ? <LoadingBlock /> : null}
        {items && !items.length ? (
          <EmptyState
            text="还没有测试运行。从一份需求开始第一次 AI 测试设计。"
            action={
              <Link className="btn btn-primary" to="/runs/new">
                新建测试运行
              </Link>
            }
          />
        ) : null}
        {items && items.length ? (
          <Card>
            <table className="table">
              <thead>
                <tr>
                  <th>需求标题</th>
                  <th>状态</th>
                  <th>创建时间</th>
                  <th>失败阶段</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr key={it.run_id} className="clickable" onClick={() => (window.location.hash = `#/runs/${it.run_id}`)}>
                    <td>{it.title}</td>
                    <td>
                      <RunStatusBadge status={it.status} />
                    </td>
                    <td className="col-muted">
                      {timeAgo(it.created_at)}（{it.created_at?.slice(0, 10)}）
                    </td>
                    <td className="col-muted">{it.failed_step ? STEP_LABEL[it.failed_step] || it.failed_step : "-"}</td>
                    <td className="col-muted">进入 →</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="muted small" style={{ marginTop: 8 }}>
              列表为最近 50 条运行（MVP 后端行为，按创建时间倒序）。
            </div>
          </Card>
        ) : null}
      </div>
    </Shell>
  );
}
