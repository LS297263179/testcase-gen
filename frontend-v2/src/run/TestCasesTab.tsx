// ============================================================
// 测试用例页（Step 5 核心资产）：状态快捷筛选 + 摘要计数条 + 列表，
// 行点击打开 Detail Drawer（编辑/重评审/Revision/Trace 均在 Drawer）。
// ============================================================

import { useMemo, useState } from "react";
import { Card, CaseStatusBadge, ProvBadge } from "../components/ui";
import { useRunBundle } from "./RunBundle";
import { CASE_STATUS_LABEL } from "../utils";

const STATUS_CHIPS = ["", "reviewed", "re_review_required", "edited", "validation_failed", "archived"];

export function TestCasesTab() {
  const { testCases, openCase } = useRunBundle();
  const [chip, setChip] = useState("");
  const [prio, setPrio] = useState("");
  const [mod, setMod] = useState("");
  const [q, setQ] = useState("");

  const counts = useMemo(() => {
    const m = new Map<string, number>();
    testCases.forEach((t) => m.set(t.status || "-", (m.get(t.status || "-") || 0) + 1));
    return m;
  }, [testCases]);
  const modules = useMemo(() => [...new Set(testCases.map((t) => t.module || ""))].sort(), [testCases]);

  const filtered = testCases.filter((t) => {
    if (chip === "archived" ? t.status !== "archived" : chip && t.status === "archived") return false;
    if (chip && t.status !== chip) return false;
    if (prio && t.priority !== prio) return false;
    if (mod && t.module !== mod) return false;
    if (q && !`${t.display_id}${t.title}${t.module}`.toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  });

  const activeTotal = testCases.length - (counts.get("archived") || 0);

  return (
    <div>
      <Card title={`测试用例（有效 ${activeTotal} · 归档 ${counts.get("archived") || 0} · 全部 ${testCases.length}）`}>
        <div className="filter-bar">
          {STATUS_CHIPS.map((s) => (
            <button key={s || "all"} className={`chip${chip === s ? " active" : ""}`} onClick={() => setChip(s)}>
              {s ? `${CASE_STATUS_LABEL[s]} ${counts.get(s) || 0}` : `全部 ${testCases.length}`}
            </button>
          ))}
          <span style={{ flex: 1 }} />
          <input type="search" placeholder="搜索编号/标题/模块..." value={q} onChange={(e) => setQ(e.target.value)} />
          <select value={prio} onChange={(e) => setPrio(e.target.value)}>
            <option value="">全部优先级</option>
            {["P0", "P1", "P2", "P3"].map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
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
        <table className="table">
          <thead>
            <tr>
              <th>编号</th>
              <th>模块</th>
              <th>标题</th>
              <th>优先级</th>
              <th>类型</th>
              <th>状态</th>
              <th>生成方式</th>
              <th>来源</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 300).map((t) => (
              <tr key={t.id} className="clickable" onClick={() => openCase(t.id)}>
                <td className="col-muted">{t.display_id}</td>
                <td>{t.module}</td>
                <td>{t.title}</td>
                <td>{t.priority}</td>
                <td className="col-muted">{t.type}</td>
                <td>
                  <CaseStatusBadge status={t.status} />
                </td>
                <td className="col-muted">{t.generation_mode}</td>
                <td>
                  <ProvBadge prov={t.provenance} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length > 300 ? <div className="muted small">显示前 300 条（共 {filtered.length} 条，请筛选）。</div> : null}
        {!filtered.length ? <div className="empty">无匹配用例。</div> : null}
      </Card>
    </div>
  );
}
