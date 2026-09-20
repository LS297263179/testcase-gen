// ============================================================
// 测试用例页（P0 重排 + P3 状态→操作强化）
// 首屏 5 秒可识别：当前用例状态 / 是否需要人工处理 / 下一步操作。
// 操作入口严格按 Step 9 状态机控制（前端只隐藏入口，校验仍由后端兜底）：
//   validation_failed → 查看 + 编辑（修复后状态转 EDITED，才能重新评审）
//   reviewed/edited/re_review_required → 查看 + 编辑 + 重新评审
//   archived → 只读查看（不物理删除，保留审计）
// 重新评审会触发真实 LLM：confirm 明示后才发请求。
// ============================================================

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { reReview } from "../api/v2";
import { useToast } from "../components/Toast";
import { CaseStatusBadge, ProvBadge } from "../components/ui";
import { useRunBundle } from "./RunBundle";
import { CASE_STATUS_LABEL } from "../utils";
import type { TestCase } from "../types";

const PENDING_SET = ["edited", "re_review_required", "validation_failed"];
const RERVIEW_SET = ["reviewed", "edited", "re_review_required"];

const CHIPS: { key: string; label: string }[] = [
  { key: "", label: "全部" },
  { key: "pending", label: "待处理" },
  { key: "reviewed", label: CASE_STATUS_LABEL.reviewed },
  { key: "re_review_required", label: CASE_STATUS_LABEL.re_review_required },
  { key: "edited", label: CASE_STATUS_LABEL.edited },
  { key: "validation_failed", label: CASE_STATUS_LABEL.validation_failed },
  { key: "confirmed", label: CASE_STATUS_LABEL.confirmed },
  { key: "archived", label: CASE_STATUS_LABEL.archived },
];

function railClass(t: TestCase): string {
  const s = t.status || "";
  if (s === "validation_failed") return "rail-danger";
  if (PENDING_SET.includes(s)) return "rail-pending";
  if (s === "archived") return "rail-muted";
  return "";
}

export function TestCasesTab() {
  const { testCases, openCase, run, reload } = useRunBundle();
  const toast = useToast();
  const [params] = useSearchParams();
  const [chip, setChip] = useState(params.get("status") || "");
  const [prio, setPrio] = useState("");
  const [mod, setMod] = useState("");
  const [q, setQ] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  // 深链变化（Dashboard「去处理」跳入）时同步筛选
  useEffect(() => {
    const s = params.get("status");
    if (s) setChip(s);
  }, [params]);

  const counts = useMemo(() => {
    const m = new Map<string, number>();
    testCases.forEach((t) => m.set(t.status || "-", (m.get(t.status || "-") || 0) + 1));
    return m;
  }, [testCases]);
  const pendingTotal = PENDING_SET.reduce((n, s) => n + (counts.get(s) || 0), 0);
  const modules = useMemo(() => [...new Set(testCases.map((t) => t.module || ""))].sort(), [testCases]);

  const filtered = testCases.filter((t) => {
    if (chip === "pending" ? !PENDING_SET.includes(t.status || "") : chip ? t.status !== chip : false) return false;
    if (prio && t.priority !== prio) return false;
    if (mod && t.module !== mod) return false;
    if (q && !`${t.display_id}${t.title}${t.module}`.toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  });

  const doReReview = async (t: TestCase) => {
    if (!window.confirm(`将对「${t.display_id} ${t.title}」触发真实 AI 重新评审（AFTER_HUMAN_EDIT），可能需要数十秒，确认继续？`)) return;
    setBusyId(t.id);
    try {
      await reReview(t.id, run.id);
      toast("重新评审完成，质量数据已更新");
      reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : "重新评审失败", "error");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div>
      <div className="filter-bar">
        {CHIPS.map((s) => {
          const n = s.key === "pending" ? pendingTotal : s.key === "" ? testCases.length : counts.get(s.key) || 0;
          if (!n && s.key) return null;
          return (
            <button key={s.key || "all"} className={`chip${chip === s.key ? " active" : ""}${s.key === "pending" ? " chip-warn" : ""}`} onClick={() => setChip(s.key)}>
              {s.label} {n}
            </button>
          );
        })}
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
            <th>状态</th>
            <th>来源</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {filtered.slice(0, 300).map((t) => {
            const s = t.status || "";
            const canEdit = s !== "archived";
            const canReReview = RERVIEW_SET.includes(s);
            return (
              <tr key={t.id} className={`${railClass(t)}`} onClick={() => openCase(t.id)}>
                <td className="col-muted">{t.display_id}</td>
                <td>{t.module}</td>
                <td>{t.title}</td>
                <td>{t.priority}</td>
                <td>
                  <CaseStatusBadge status={t.status} />
                </td>
                <td>
                  <ProvBadge prov={t.provenance} />
                </td>
                <td onClick={(e) => e.stopPropagation()} style={{ whiteSpace: "nowrap" }}>
                  <button className="btn btn-sm btn-ghost" onClick={() => openCase(t.id)}>
                    查看
                  </button>
                  {canEdit ? (
                    <button className="btn btn-sm btn-ghost" onClick={() => openCase(t.id, "edit")}>
                      编辑
                    </button>
                  ) : null}
                  {canReReview ? (
                    <button className="btn btn-sm btn-ghost" onClick={() => doReReview(t)} disabled={busyId === t.id}>
                      {busyId === t.id ? "评审中..." : "重新评审"}
                    </button>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {filtered.length > 300 ? <div className="t-aux">显示前 300 条（共 {filtered.length} 条，请筛选）。</div> : null}
      {!filtered.length ? <div className="empty">无匹配用例。</div> : null}
      <div className="t-aux" style={{ marginTop: 10 }}>
        左侧色轨标记处理优先级：橙=待人工处理 · 红=校验失败（编辑修复后方可重新评审）· 灰=已归档（只读）。
      </div>
    </div>
  );
}
