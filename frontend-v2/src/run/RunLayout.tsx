// ============================================================
// 运行详情布局：面包屑 + 二级 Tab + 数据总线 + Drawer 宿主。
// Tab 即工作流阶段：概览 → 需求 → 测试点 → 测试用例 → AI 评审 → 优化 → 报告。
// ============================================================

import { NavLink, Outlet, useParams } from "react-router-dom";
import { Shell } from "../layout/Shell";
import { RunBundleProvider, useRunBundle } from "./RunBundle";
import { CaseDrawer } from "./CaseDrawer";
import { RunStatusBadge } from "../components/ui";

export function RunLayout() {
  const { runId } = useParams<{ runId: string }>();
  if (!runId) return null;
  return (
    <RunBundleProvider runId={runId}>
      <RunLayoutInner />
    </RunBundleProvider>
  );
}

function RunLayoutInner() {
  const { run, title, testPoints, testCases, review, optimizer } = useRunBundle();
  const base = `/runs/${run.id}`;
  const tabs = [
    { to: base, label: "概览", end: true },
    { to: `${base}/requirements`, label: "需求与 AI 分析", count: run.counts?.items },
    { to: `${base}/test-points`, label: "测试点", count: testPoints.length },
    { to: `${base}/test-cases`, label: "测试用例", count: testCases.length },
    { to: `${base}/review`, label: "AI 评审", count: review?.findings?.length },
    { to: `${base}/optimizer`, label: "优化", count: optimizer?.archived_count },
    { to: `${base}/report`, label: "报告" },
  ];
  return (
    <Shell
      crumbs={
        <>
          <NavLink to="/runs">测试运行</NavLink> <span>/</span> <b>{title}</b>
        </>
      }
    >
      <div className="page">
        <div className="page-header">
          <div className="ph-main">
            <h2>
              {title} <RunStatusBadge status={run.status} />
            </h2>
            <div className="ph-desc">
              一次 Step 2→8 Runtime 流水线执行 · 创建 {run.created_at || "-"}
            </div>
          </div>
          <div className="ph-actions no-print">
            <button className="btn btn-sm btn-outline" onClick={() => window.print()}>
              打印 / 导出 PDF
            </button>
          </div>
        </div>
        <div className="tabs no-print">
          {tabs.map((t) => (
            <NavLink key={t.to} className={({ isActive }) => `tab${isActive ? " active" : ""}`} to={t.to} end={t.end}>
              {t.label}
              {t.count != null ? <span className="tab-count">{t.count}</span> : null}
            </NavLink>
          ))}
        </div>
        <Outlet />
      </div>
      <CaseDrawer />
    </Shell>
  );
}
