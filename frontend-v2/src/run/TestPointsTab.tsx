// ============================================================
// 测试点页（Step 3 + Step 4 的呈现）：
// 顶部摘要回答「AI 生成了什么、规则补了什么、覆盖了多少」，下面才是列表。
// ============================================================

import { useMemo, useState } from "react";
import { Card, ProvBadge } from "../components/ui";
import { useRunBundle } from "./RunBundle";

export function TestPointsTab() {
  const { testPoints, run } = useRunBundle();
  const [prov, setProv] = useState<string>("");
  const [mod, setMod] = useState<string>("");
  const [q, setQ] = useState("");

  const stats = useMemo(() => {
    const llm = testPoints.filter((p) => (p.provenance || "").toUpperCase() === "LLM").length;
    const strategy = testPoints.filter((p) => (p.provenance || "").toUpperCase() === "STRATEGY").length;
    const scope = new Map<string, number>();
    testPoints.forEach((p) => {
      const s = p.generation_scope || "item";
      scope.set(s, (scope.get(s) || 0) + 1);
    });
    const dims = new Map<string, number>();
    testPoints.forEach((p) => dims.set(p.dimension || "-", (dims.get(p.dimension || "-") || 0) + 1));
    return { llm, strategy, scope, dims };
  }, [testPoints]);

  const modules = useMemo(() => [...new Set(testPoints.map((p) => p.module))].sort(), [testPoints]);

  const filtered = testPoints.filter((p) => {
    if (prov && (p.provenance || "").toUpperCase() !== prov) return false;
    if (mod && p.module !== mod) return false;
    if (q && !`${p.title}${p.description}${p.subcategory}`.toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  });

  return (
    <div>
      <Card title={`测试点设计来源（共 ${testPoints.length} 个）`}>
        <div className="grid-4">
          <div className="metric">
            <div className="m-label">LLM 语义设计（Step 3）</div>
            <div className="m-value">{stats.llm}</div>
            <div className="m-sub">
              单需求项 {stats.scope.get("item") ?? 0} · 跨项联动 {stats.scope.get("cross_item") ?? 0}
            </div>
          </div>
          <div className="metric">
            <div className="m-label">Strategy 确定性补充（Step 4）</div>
            <div className="m-value">{stats.strategy}</div>
            <div className="m-sub">边界值 / 等价类 / 权限矩阵（代码派生）</div>
          </div>
          <div className="metric">
            <div className="m-label">覆盖义务</div>
            <div className="m-value">{run.counts?.obligations ?? 0}</div>
            <div className="m-sub">策略引擎派生的必测项</div>
          </div>
          <div className="metric">
            <div className="m-label">技术分布</div>
            <div className="m-value small" style={{ fontSize: 14, lineHeight: 1.6 }}>
              {[...stats.dims.entries()].map(([k, v]) => `${k} ${v}`).join(" · ") || "-"}
            </div>
          </div>
        </div>
      </Card>

      <Card
        title="测试点列表"
        extra={
          <div className="filter-bar" style={{ margin: 0 }}>
            <input type="search" placeholder="搜索标题/描述/子分类..." value={q} onChange={(e) => setQ(e.target.value)} />
            <select value={prov} onChange={(e) => setProv(e.target.value)}>
              <option value="">全部来源</option>
              <option value="LLM">LLM</option>
              <option value="STRATEGY">STRATEGY</option>
            </select>
            <select value={mod} onChange={(e) => setMod(e.target.value)}>
              <option value="">全部模块</option>
              {modules.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
        }
      >
        <table className="table">
          <thead>
            <tr>
              <th>模块</th>
              <th>子分类</th>
              <th>标题</th>
              <th>描述</th>
              <th>维度</th>
              <th>优先级</th>
              <th>技术</th>
              <th>来源</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 300).map((p) => (
              <tr key={p.id}>
                <td>{p.module}</td>
                <td className="col-muted">{p.subcategory || "-"}</td>
                <td>{p.title}</td>
                <td className="col-muted" title={p.description}>
                  {(p.description || "").slice(0, 60)}
                  {(p.description || "").length > 60 ? "..." : ""}
                </td>
                <td>{p.dimension || "-"}</td>
                <td>{p.priority || "-"}</td>
                <td className="col-muted">{p.technique || "-"}</td>
                <td>
                  <ProvBadge prov={p.provenance} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length > 300 ? <div className="muted small">显示前 300 条（共 {filtered.length} 条，请用筛选缩小范围）。</div> : null}
        {!filtered.length ? <div className="empty">无匹配测试点。</div> : null}
      </Card>
    </div>
  );
}
