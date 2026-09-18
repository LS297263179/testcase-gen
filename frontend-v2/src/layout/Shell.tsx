// ============================================================
// App Shell：侧边导航（工作台 / 测试运行 / 资源 / 设置）+ 顶栏面包屑。
// IA 主线：工作台 → 测试运行 → (Tab) 需求 / 测试设计 / Review / 优化 / 报告。
// ============================================================

import { NavLink } from "react-router-dom";
import { useAuth } from "../context/Auth";
import type { ReactNode } from "react";

function Nav() {
  return (
    <nav className="sidebar-nav">
      <NavLink className="nav-item" to="/" end>
        <span className="nav-icon">▦</span> 工作台
      </NavLink>
      <div className="nav-group-label">核心工作流</div>
      <NavLink className="nav-item" to="/runs">
        <span className="nav-icon">▶</span> 测试运行
      </NavLink>
      <div className="nav-sub">
        <NavLink className="nav-item" to="/runs/new">
          <span className="nav-icon">＋</span> 新建测试运行
        </NavLink>
      </div>
      <div className="nav-group-label">资源</div>
      <div className="nav-sub">
        <NavLink className="nav-item" to="/resources/materials">
          <span className="nav-icon">▤</span> 项目材料
        </NavLink>
        <NavLink className="nav-item" to="/resources/xmind">
          <span className="nav-icon">✎</span> XMind 与迁移
        </NavLink>
      </div>
      <div className="nav-group-label">平台</div>
      <NavLink className="nav-item" to="/settings">
        <span className="nav-icon">⚙</span> 系统设置
      </NavLink>
    </nav>
  );
}

export function Shell({ crumbs, children }: { crumbs: ReactNode; children: ReactNode }) {
  const { username } = useAuth();
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="brand-title">AI 测试工程平台</div>
          <div className="brand-sub">V2 · AI + 规则 + 人 协同测试设计</div>
        </div>
        <Nav />
        <div className="sidebar-footer">
          <div className="who">{username}</div>
          <a href="/" style={{ color: "#94a3b8", fontSize: 11.5 }}>
            返回 V1
          </a>
          <button
            style={{ marginLeft: 8 }}
            onClick={async () => {
              try {
                await fetch("/api/logout", { method: "POST" });
              } catch {
                /* 忽略 */
              }
              window.location.href = "/";
            }}
          >
            退出
          </button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="crumbs">{crumbs}</div>
        </header>
        {children}
      </div>
    </div>
  );
}
