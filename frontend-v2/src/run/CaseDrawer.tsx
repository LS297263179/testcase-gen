// ============================================================
// TestCase 详情 Drawer（Step 5/6/7/9 能力的产品落点）：
// 基本信息 / 结构化步骤 / 测试数据计划 / 本用例 finding / 追溯链(Step 6)
// / 修订历史(Step 9) / 人工编辑(乐观锁+409) / 重新评审(AFTER_HUMAN_EDIT)
// ============================================================

import { useEffect, useState } from "react";
import { editTestCase, getTrace, listRevisions, reReview } from "../api/v2";
import { useToast } from "../components/Toast";
import { ApiError } from "../api/client";
import { CaseStatusBadge, KV, ProvBadge } from "../components/ui";
import { useRunBundle } from "./RunBundle";
import type { CaseRevision, TestCase, TraceResult } from "../types";
import { CASE_STATUS_LABEL } from "../utils";

export function CaseDrawer() {
  const bundle = useRunBundle();
  const { caseDrawerId, openCase, testCases, review, run, reload } = bundle;
  const toast = useToast();
  const [revs, setRevs] = useState<CaseRevision[] | null>(null);
  const [trace, setTrace] = useState<TraceResult | null>(null);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);

  const tc = caseDrawerId ? testCases.find((c) => c.id === caseDrawerId) : null;

  useEffect(() => {
    if (!caseDrawerId) {
      setRevs(null);
      setTrace(null);
      return;
    }
    let cancelled = false;
    Promise.allSettled([listRevisions(caseDrawerId), getTrace(caseDrawerId)]).then(([r, t]) => {
      if (cancelled) return;
      setRevs(r.status === "fulfilled" ? r.value : []);
      setTrace(t.status === "fulfilled" ? t.value : null);
    });
    return () => {
      cancelled = true;
    };
  }, [caseDrawerId, tc?.updated_at]);

  if (!caseDrawerId) return null;

  const findings = (review?.findings || []).filter((f) => f.target_id === caseDrawerId);

  const doReReview = async () => {
    setBusy(true);
    try {
      await reReview(caseDrawerId, run.id);
      toast("重新评审完成（触发 AFTER_HUMAN_EDIT 轮次）");
      reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : "重新评审失败", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) openCase(null);
      }}
    >
      <div className="drawer">
        <div className="drawer-header">
          <h3>
            {tc ? `${tc.display_id} · ${tc.title}` : "TestCase"}
          </h3>
          {tc ? <CaseStatusBadge status={tc.status} /> : null}
          <button className="modal-close" style={{ marginLeft: "auto" }} onClick={() => openCase(null)} aria-label="关闭">
            ×
          </button>
        </div>
        <div className="drawer-body">
          {!tc ? (
            <div className="alert alert-info">该用例不在当前运行数据中（可能属于其他运行）。</div>
          ) : (
            <>
              <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
                <button className="btn btn-sm btn-outline" onClick={() => setEditing(true)}>
                  编辑（人工确认 · Step 9）
                </button>
                <button className="btn btn-sm btn-outline" onClick={doReReview} disabled={busy}>
                  {busy ? "评审中..." : "重新评审（阶段 B）"}
                </button>
              </div>

              <div className="kv-grid">
                <KV k="模块" v={tc.module || "-"} />
                <KV k="优先级" v={tc.priority || "-"} />
                <KV k="类型" v={tc.type || "-"} />
                <KV k="状态" v={CASE_STATUS_LABEL[tc.status || ""] || tc.status || "-"} />
                <KV k="来源" v={<ProvBadge prov={tc.provenance} />} />
                <KV k="生成方式" v={tc.generation_mode || "-"} />
                <KV k="置信度" v={tc.confidence_level || "-"} />
              </div>

              <Field label="前置条件" value={tc.precondition || "（无）"} />
              <div className="field-block">
                <div className="fb-label">测试步骤</div>
                {(tc.steps || []).length ? (
                  <ol className="steps">
                    {[...(tc.steps || [])].sort((a, b) => a.seq - b.seq).map((s) => (
                      <li key={s.seq}>
                        {s.action}
                        {s.data ? `（输入：${s.data}）` : ""}
                        {s.expected ? ` → ${s.expected}` : ""}
                      </li>
                    ))}
                  </ol>
                ) : (
                  <div className="muted small">无</div>
                )}
              </div>
              <Field label="预期结果" value={tc.expected || "（无）"} />
              {tc.remark ? <Field label="备注" value={tc.remark} /> : null}

              {(tc.data_plan || []).length ? (
                <div className="field-block">
                  <div className="fb-label">测试数据计划（DataPlan · Step 5）</div>
                  <table className="table">
                    <thead>
                      <tr>
                        <th>字段</th>
                        <th>策略</th>
                        <th>值</th>
                        <th>来源</th>
                        <th>生成器</th>
                        <th>预期合法</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tc.data_plan!.map((dp, i) => (
                        <tr key={i}>
                          <td>{dp.field}</td>
                          <td>{dp.strategy}</td>
                          <td>{String(dp.value ?? "-")}</td>
                          <td className="col-muted">{dp.source}</td>
                          <td className="col-muted">{dp.generator}</td>
                          <td>{dp.expected_valid === false ? "否" : "是"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}

              {(tc.validation_errors || []).length ? (
                <div className="alert alert-error">
                  <b>校验错误：</b>
                  {tc.validation_errors!.join("；")}
                </div>
              ) : null}

              <div className="section-title">AI 评审发现（本用例）</div>
              {findings.length ? (
                findings.map((f, i) => (
                  <div key={i} className={`finding sev-${(f.severity || "").toLowerCase()}`}>
                    <div className="f-head">
                      <span className="f-dim">{f.dimension}</span>
                      <span className="f-sev">{f.severity}</span>
                      <ProvBadge prov={f.provenance} />
                      {f.auto_fixable ? <span className="f-autofix">auto_fixable</span> : null}
                    </div>
                    <div className="f-issue">{f.issue}</div>
                    {f.suggestion ? <div className="f-sug">建议：{f.suggestion}</div> : null}
                  </div>
                ))
              ) : (
                <div className="muted small">
                  {review ? "本用例无相关 finding。" : "该运行暂无评审报告。"}
                </div>
              )}

              <div className="section-title">追溯链（需求项 → 测试点 → 本用例 · Step 6）</div>
              {trace ? <TraceView trace={trace} /> : <div className="muted small">加载中或不可用。</div>}

              <div className="section-title">修订历史（Revision · Step 9）</div>
              {revs === null ? (
                <div className="muted small">加载中...</div>
              ) : revs.length ? (
                <table className="table">
                  <thead>
                    <tr>
                      <th>版本</th>
                      <th>来源</th>
                      <th>修改字段</th>
                      <th>修改人</th>
                      <th>时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {revs.map((r, i) => (
                      <tr key={i}>
                        <td>#{r.revision_no}</td>
                        <td>
                          <ProvBadge prov={r.provenance} />
                        </td>
                        <td>{(r.changed_fields || []).join(", ") || "-"}</td>
                        <td className="col-muted">
                          {r.changed_by || "-"} / {r.change_source || "-"}
                        </td>
                        <td className="col-muted">{r.created_at}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="muted small">无修订历史（尚未人工编辑）。</div>
              )}

              {editing && tc ? (
                <CaseEditModal
                  tc={tc}
                  onClose={() => setEditing(false)}
                  onSaved={() => {
                    setEditing(false);
                    reload();
                  }}
                />
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="field-block">
      <div className="fb-label">{label}</div>
      <div className="fb-value">{value}</div>
    </div>
  );
}

function TraceView({ trace }: { trace: TraceResult }) {
  const items = trace.requirement_items || [];
  const tps = trace.test_points || [];
  return (
    <div className="trace">
      <div className="trace-node">
        <div className="trace-label">RequirementItem（{items.length}）</div>
        {items.length ? (
          items.map((it, i) => (
            <div key={i} className="trace-card">
              [{it.type || "-"}] {it.module} · {it.statement}
            </div>
          ))
        ) : (
          <span className="muted">无</span>
        )}
      </div>
      <div className="trace-arrow">↓</div>
      <div className="trace-node">
        <div className="trace-label">TestPoint（{tps.length}）</div>
        {tps.length ? (
          tps.map((tp, i) => (
            <div key={i} className="trace-card">
              <ProvBadge prov={tp.provenance} /> {tp.title}
            </div>
          ))
        ) : (
          <span className="muted">无</span>
        )}
      </div>
      <div className="trace-arrow">↓</div>
      <div className="trace-node">
        <div className="trace-label">TestCase</div>
        <div className="trace-card">
          {trace.test_case?.display_id} · {trace.test_case?.title}
        </div>
      </div>
      {trace.doc || trace.version ? (
        <div className="muted small">
          需求文档：{trace.doc?.title || "-"} · 版本 v{trace.version?.version_no ?? "-"}
        </div>
      ) : null}
      {(trace.issues || []).length ? <div className="alert alert-error">{trace.issues!.join("；")}</div> : null}
    </div>
  );
}

// ---------------- 编辑弹窗（Step 9：白名单字段 + 乐观锁） ----------------

function CaseEditModal({ tc, onClose, onSaved }: { tc: TestCase; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [title, setTitle] = useState(tc.title || "");
  const [module, setModule] = useState(tc.module || "");
  const [priority, setPriority] = useState(tc.priority || "P1");
  const [precondition, setPrecondition] = useState(tc.precondition || "");
  const [expected, setExpected] = useState(tc.expected || "");
  const [remark, setRemark] = useState(tc.remark || "");
  const stepsOriginal = [...(tc.steps || [])].sort((a, b) => a.seq - b.seq).map((s) => s.action).join("\n");
  const [steps, setSteps] = useState(stepsOriginal);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    setErr(null);
    const updates: Record<string, unknown> = { title, module, priority, precondition, expected, remark };
    // 仅当步骤实际被修改时提交 steps（与旧版一致，避免无意丢失结构化子字段）
    if (steps !== stepsOriginal) {
      updates.steps = steps
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean)
        .map((a, i) => ({ seq: i + 1, action: a }));
    }
    try {
      const res = await editTestCase(tc.id, updates, tc.updated_at || "");
      if (res.success === false) {
        setErr("保存未生效：" + ((res.issues || res.validation_errors || []).join("；") || "未知原因"));
        return;
      }
      toast(`保存成功，状态 → ${CASE_STATUS_LABEL[res.new_status || ""] || res.new_status || "-"}`);
      onSaved();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setErr("编辑冲突（409）：用例已被更新，请关闭后重新打开编辑。");
      } else {
        setErr(e instanceof Error ? e.message : "保存失败");
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-head">
          <h3>编辑 TestCase（{tc.display_id}）</h3>
          <button className="modal-close" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </div>
        <div className="modal-body">
          <div className="form-group">
            <label>标题</label>
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="form-group">
            <label>模块</label>
            <input type="text" value={module} onChange={(e) => setModule(e.target.value)} />
          </div>
          <div className="form-group">
            <label>优先级</label>
            <select value={priority} onChange={(e) => setPriority(e.target.value)}>
              {["P0", "P1", "P2", "P3"].map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label>前置条件</label>
            <textarea value={precondition} onChange={(e) => setPrecondition(e.target.value)} />
          </div>
          <div className="form-group">
            <label>步骤（每行一步）</label>
            <textarea value={steps} onChange={(e) => setSteps(e.target.value)} />
            <div className="hint">编辑步骤将简化为纯动作描述；不修改则保留原结构化步骤。</div>
          </div>
          <div className="form-group">
            <label>预期结果</label>
            <textarea value={expected} onChange={(e) => setExpected(e.target.value)} />
          </div>
          <div className="form-group">
            <label>备注</label>
            <textarea value={remark} onChange={(e) => setRemark(e.target.value)} />
          </div>
          {err ? <div className="alert alert-error">{err}</div> : null}
        </div>
        <div className="modal-foot">
          <button className="btn btn-ghost" onClick={onClose}>
            取消
          </button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? "保存中..." : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
