// ============================================================
// 资源 · 项目材料：复用 V1 现有 /api/materials 端点（V1 运行时零改动）。
// 诚实标注：V2 生成时引用材料暂未接入（用户冻结决策）。
// ============================================================

import { useEffect, useState } from "react";
import { createMaterial, deleteMaterial, getMaterial, listMaterials } from "../api/v1";
import { Card, EmptyState, ErrorAlert, LoadingBlock } from "../components/ui";
import { useToast } from "../components/Toast";
import { Shell } from "../layout/Shell";
import type { MaterialDetail, MaterialListItem } from "../types";

export function Materials() {
  const [items, setItems] = useState<MaterialListItem[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [detail, setDetail] = useState<MaterialDetail | null>(null);
  const [creating, setCreating] = useState(false);
  const toast = useToast();

  const load = () => {
    setError(null);
    listMaterials()
      .then(setItems)
      .catch(setError);
  };
  useEffect(load, []);

  const remove = async (id: number) => {
    if (!window.confirm("确认删除该材料？（V1 资产库操作，不可恢复）")) return;
    try {
      await deleteMaterial(id);
      toast("已删除");
      load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "删除失败", "error");
    }
  };

  return (
    <Shell crumbs={<><span>资源</span> <span>/</span> <b>项目材料</b></>}>
      <div className="page">
        <div className="page-header">
          <div className="ph-main">
            <h2>项目材料</h2>
            <div className="ph-desc">与 V1 共用的材料库（标题 + 文本 + 图片）。此处管理所有材料；V1 生成流程已支持引用。</div>
          </div>
          <div className="ph-actions">
            <button className="btn btn-primary" onClick={() => setCreating(true)}>
              ＋ 新建材料
            </button>
          </div>
        </div>

        <div className="alert alert-warn">
          注意：<b>V2 生成时引用项目材料暂未接入</b>（未来将单独设计 Material → Requirement Context → Runtime 后端链路）。
          当前 V2 测试运行仅使用输入的需求文本。
        </div>

        {error ? <ErrorAlert error={error} onRetry={load} /> : null}
        {items === null ? (
          <LoadingBlock />
        ) : items.length ? (
          <Card>
            <table className="table">
              <thead>
                <tr>
                  <th>标题</th>
                  <th>内容预览</th>
                  <th>图片</th>
                  <th>创建时间</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {items.map((m) => (
                  <tr key={m.id}>
                    <td>{m.title}</td>
                    <td className="col-muted">{m.content_preview || "-"}</td>
                    <td className="col-muted">{m.image_count ?? 0}</td>
                    <td className="col-muted">{m.created_at || "-"}</td>
                    <td>
                      <button
                        className="btn btn-sm btn-ghost"
                        onClick={async () => setDetail(await getMaterial(m.id).catch(() => null))}
                      >
                        查看
                      </button>
                      <button className="btn btn-sm btn-ghost" onClick={() => remove(m.id)}>
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ) : (
          <EmptyState text="暂无项目材料。" action={<button className="btn btn-sm btn-primary" onClick={() => setCreating(true)}>新建材料</button>} />
        )}

        {creating ? <MaterialCreateModal onClose={() => setCreating(false)} onCreated={() => { setCreating(false); load(); }} /> : null}
        {detail ? (
          <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && setDetail(null)}>
            <div className="modal">
              <div className="modal-head">
                <h3>{detail.title}</h3>
                <button className="modal-close" onClick={() => setDetail(null)}>×</button>
              </div>
              <div className="modal-body">
                <div className="fb-value" style={{ whiteSpace: "pre-wrap" }}>{detail.content || "（无文本内容）"}</div>
                {(detail.images || []).length ? (
                  <div style={{ marginTop: 10 }}>
                    <div className="t-section" style={{ marginBottom: 6 }}>图片（{detail.images!.length}）</div>
                    <div className="muted small">图片存储于 V1 资产库，V1 页面可查看原图。</div>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </Shell>
  );
}

function MaterialCreateModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const toast = useToast();

  const save = async () => {
    if (!title.trim()) {
      setErr("标题不能为空");
      return;
    }
    setBusy(true);
    try {
      await createMaterial(title.trim(), content, files);
      toast("材料已创建");
      onCreated();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "创建失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-head">
          <h3>新建项目材料</h3>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          <div className="form-group">
            <label>标题</label>
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="form-group">
            <label>内容（文本/Markdown）</label>
            <textarea value={content} onChange={(e) => setContent(e.target.value)} />
          </div>
          <div className="form-group">
            <label>图片（可选）</label>
            <input
              type="file"
              accept="image/*"
              multiple
              onChange={(e) => setFiles(Array.from(e.target.files || []))}
            />
          </div>
          {err ? <div className="alert alert-error">{err}</div> : null}
        </div>
        <div className="modal-foot">
          <button className="btn btn-ghost" onClick={onClose}>取消</button>
          <button className="btn btn-primary" onClick={save} disabled={busy}>
            {busy ? "保存中..." : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
