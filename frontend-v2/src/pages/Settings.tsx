// ============================================================
// 系统设置：V2 运行状态（/api/v2/health）+ 模型配置（复用 V1 /api/model-config）。
// 模型配置默认收起高级参数，减少暴露感（redesign 文档 §18 方向）。
// ============================================================

import { useEffect, useState } from "react";
import { getModelConfig, saveModelConfig } from "../api/v1";
import { getHealth } from "../api/v2";
import { Card, LoadingBlock } from "../components/ui";
import { useToast } from "../components/Toast";
import { Shell } from "../layout/Shell";
import type { ModelConfig } from "../api/v1";

export function Settings() {
  const [health, setHealth] = useState<{ status: string; v2_ready: boolean; schema_version: number | null } | null>(null);
  const [cfg, setCfg] = useState<ModelConfig | null>(null);
  const [presets, setPresets] = useState<Record<string, string>>({});
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  // ★ 安全护栏：后端返回的完整 api_key 不回显、不回流；用户输入新 Key 时才覆盖（空值后端沿用原值）。
  const [newGenKey, setNewGenKey] = useState("");
  const toast = useToast();

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth({ status: "error", v2_ready: false, schema_version: null }));
    getModelConfig().then((d) => {
      setCfg(d.config);
      setPresets(d.presets);
    }).catch(() => setCfg({}));
  }, []);

  const gen = (cfg?.generate || {}) as Record<string, unknown>;
  const rev = (cfg?.review || {}) as Record<string, unknown>;

  const setSection = (section: "generate" | "review", key: string, value: unknown) => {
    setCfg((c) => {
      const cur = (c?.[section] || {}) as Record<string, unknown>;
      return { ...(c || {}), [section]: { ...cur, [key]: value } } as ModelConfig;
    });
  };

  const save = async () => {
    if (!cfg) return;
    setBusy(true);
    try {
      const payload = { ...cfg } as ModelConfig;
      if (newGenKey.trim()) {
        payload.generate = { ...((payload.generate || {}) as Record<string, unknown>), api_key: newGenKey.trim() };
      }
      await saveModelConfig(payload);
      setNewGenKey("");
      toast("模型配置已保存（V1/V2 共用同一配置源）");
    } catch (e) {
      toast(e instanceof Error ? e.message : "保存失败", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Shell crumbs={<b>系统设置</b>}>
      <div className="page" style={{ maxWidth: 860 }}>
        <div className="page-header">
          <div className="ph-main">
            <h2>系统设置</h2>
            <div className="ph-desc">平台配置不属于测试工作主路径；模型配置与 V1 共用同一配置源。</div>
          </div>
        </div>

        <Card title="V2 运行状态">
          {health === null ? (
            <LoadingBlock />
          ) : (
            <div className="kv-grid">
              <div className="kv">
                <span className="k">服务状态</span>
                <span className="v">{health.status === "ok" ? "正常" : "降级（V2 未就绪）"}</span>
              </div>
              <div className="kv">
                <span className="k">V2_READY</span>
                <span className="v">{String(health.v2_ready)}</span>
              </div>
              <div className="kv">
                <span className="k">schema_version</span>
                <span className="v">{health.schema_version ?? "-"}</span>
              </div>
            </div>
          )}
        </Card>

        <Card title="模型配置" extra={<button className="btn btn-sm btn-ghost" onClick={() => setAdvanced(!advanced)}>{advanced ? "收起高级参数" : "展开高级参数"}</button>}>
          {cfg === null ? (
            <LoadingBlock />
          ) : (
            <>
              <div className="grid-2">
                <div className="form-group">
                  <label>生成模型（需求解析 / 测试点 / 用例合成）</label>
                  <input type="text" value={String(gen.model || "")} onChange={(e) => setSection("generate", "model", e.target.value)} />
                  {/* P5：API Key 只显示已配置/未配置，不展示任何可识别内容 */}
                  <div className="hint">
                    API Key：<span className={`badge ${gen.api_key_hint ? "badge-run-done" : "badge-run-neutral"}`}>{gen.api_key_hint ? "已配置" : "未配置"}</span>
                  </div>
                  {advanced ? (
                    <>
                      <div className="hint">api_type：{String(gen.api_type || "-")}</div>
                      <input type="text" value={String(gen.base_url || "")} onChange={(e) => setSection("generate", "base_url", e.target.value)} placeholder="base_url" style={{ marginTop: 6 }} />
                      <input
                        type="password"
                        value={newGenKey}
                        onChange={(e) => setNewGenKey(e.target.value)}
                        placeholder="输入新 Key 才会更新；不修改则留空"
                        style={{ marginTop: 6 }}
                        autoComplete="off"
                      />
                      <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                        <input type="number" step="0.1" value={String(gen.temperature ?? 0.3)} onChange={(e) => setSection("generate", "temperature", Number(e.target.value))} placeholder="temperature" />
                        <input type="number" value={String(gen.max_tokens ?? 4096)} onChange={(e) => setSection("generate", "max_tokens", Number(e.target.value))} placeholder="max_tokens" />
                      </div>
                    </>
                  ) : null}
                </div>
                <div className="form-group">
                  <label>评审模型（AI Reviewer）</label>
                  <input type="text" value={String(rev.model || "")} onChange={(e) => setSection("review", "model", e.target.value)} />
                  <div className="hint">
                    enabled：{String(rev.enabled ?? true)} · 评审 Key：
                    <span className={`badge ${rev.api_key_hint ? "badge-run-done" : "badge-run-neutral"}`}>{rev.api_key_hint ? "已配置" : "未配置（沿用生成 Key）"}</span>
                  </div>
                  {advanced ? (
                    <div className="muted small" style={{ marginTop: 6 }}>
                      评审 Key 如需修改请使用 V1 页面完整配置（避免在 V2 页回显密钥）。
                    </div>
                  ) : null}
                </div>
              </div>
              <div className="muted small">预设模型（高级）：{Object.values(presets).join(" / ")}</div>
              <hr className="divider" />
              <button className="btn btn-primary" onClick={save} disabled={busy}>
                {busy ? "保存中..." : "保存配置"}
              </button>
              <div className="hint muted small" style={{ marginTop: 8 }}>
                配置保存后立即对下一次 V2 运行生效（与 V1 共用 DB model_config）；V2 代码与提示词变更仍需重启应用。
              </div>
            </>
          )}
        </Card>
      </div>
    </Shell>
  );
}
