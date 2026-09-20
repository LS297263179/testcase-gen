// ============================================================
// 通用展示组件（P0 视觉体系）
// 容器三级：Section（默认，无边框）/ Panel（浅底辅助）/ Card（重要独立分组才用）
// 颜色只承担：状态 / 来源（AI/Strategy）/ 操作优先级 / 选中态。
// ============================================================

import type { ReactNode } from "react";
import { CASE_STATUS_LABEL, RUN_STATUS_LABEL, fmtTime, pct, scoreLevel } from "../utils";

export function RunStatusBadge({ status }: { status?: string }) {
  const s = (status || "").toLowerCase();
  const cls = s === "done" ? "badge-run-done" : s === "failed" ? "badge-run-failed" : s ? "badge-run-running" : "badge-run-neutral";
  return <span className={`badge ${cls}`}>{RUN_STATUS_LABEL[s] || s || "-"}</span>;
}

export function CaseStatusBadge({ status }: { status?: string }) {
  const s = (status || "").toLowerCase();
  return <span className={`badge badge-st-${s}`}>{CASE_STATUS_LABEL[s] || s || "-"}</span>;
}

/** 来源 chip：LLM→AI Violet / STRATEGY→Teal（低饱和，仅识别用途） */
export function ProvBadge({ prov }: { prov?: string }) {
  const p = (prov || "").toLowerCase();
  const cls = p === "llm" ? "prov-llm" : p === "strategy" ? "prov-strategy" : "prov-other";
  return <span className={`prov ${cls}`}>{(prov || "-").toUpperCase()}</span>;
}

/** AI 触点小标识：文字级，无图标/渐变 */
export function AIChip({ label = "AI" }: { label?: string }) {
  return <span className="ai-chip">{label}</span>;
}

export function SevTag({ severity }: { severity: string }) {
  return <span className={`sev-tag ${(severity || "").toLowerCase()}`}>{severity}</span>;
}

export function ScoreBar({ label, value, suffix, attention }: { label: string; value?: number | null; suffix?: string; attention?: boolean }) {
  const level = scoreLevel(value);
  return (
    <div className="score-item">
      <div className="score-label">
        <span>
          {label} <b>{value == null ? "-" : value}</b>
          {suffix ? <span className="muted"> {suffix}</span> : null}
          {attention ? <span className="attn"> ← 关注</span> : null}
        </span>
      </div>
      <div className="bar">
        <div className={`bar-fill ${level}`} style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }} />
      </div>
    </div>
  );
}

export function CoverageBar({ label, value }: { label: string; value?: number | null }) {
  return (
    <div className="score-item">
      <div className="score-label">
        <span>
          {label} <b>{pct(value) ?? "N/A"}</b>
        </span>
      </div>
      <div className="bar">
        <div className="bar-fill cov" style={{ width: `${Math.max(0, Math.min(100, (value ?? 0) * 100))}%` }} />
      </div>
    </div>
  );
}

/** Section：默认内容组织方式（标题 + 分隔线，无卡片边框） */
export function Section({
  title,
  hint,
  actions,
  children,
}: {
  title?: ReactNode;
  hint?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="section">
      {(title || actions) && (
        <div className="section-head">
          {title ? <span className="t-section">{title}</span> : null}
          {hint ? <span className="section-hint">{hint}</span> : null}
          {actions ? <div className="section-actions">{actions}</div> : null}
        </div>
      )}
      {children}
    </section>
  );
}

/** Panel：浅底色块（辅助信息） */
export function Panel({ children, line }: { children: ReactNode; line?: boolean }) {
  return <div className={line ? "panel-line" : "panel"}>{children}</div>;
}

/** Card：仅真正需要视觉独立分组时使用 */
export function Card({ title, extra, children }: { title?: ReactNode; extra?: ReactNode; children: ReactNode }) {
  return (
    <div className="card">
      {(title || extra) && (
        <div className="card-header">
          {title ? <h3>{title}</h3> : null}
          {extra ? <div className="ch-extra">{extra}</div> : null}
        </div>
      )}
      <div className="card-body">{children}</div>
    </div>
  );
}

export interface StatItem {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "default" | "hl" | "ok" | "warn" | "bad";
}

/** StatStrip：横向统计条（替代统计卡片墙） */
export function StatStrip({ items }: { items: StatItem[] }) {
  return (
    <div className="stat-strip">
      {items.map((it, i) => (
        <div key={i} className={`stat-item${it.tone && it.tone !== "default" ? ` ${it.tone}` : ""}`}>
          <div className="s-label">{it.label}</div>
          <div className="s-value">
            {it.value}
            {it.sub ? <small>{it.sub}</small> : null}
          </div>
        </div>
      ))}
    </div>
  );
}

export function EmptyState({ text, action }: { text: string; action?: ReactNode }) {
  return (
    <div className="empty">
      {text}
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  );
}

export function LoadingBlock({ text = "加载中..." }: { text?: string }) {
  return (
    <div className="loading-block">
      <span className="spinner" /> {text}
    </div>
  );
}

export function ErrorAlert({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof Error ? error.message : String(error ?? "加载失败");
  return (
    <div className="alert alert-error">
      {msg}
      {onRetry ? (
        <button className="btn btn-sm btn-outline" style={{ marginLeft: 10 }} onClick={onRetry}>
          重试
        </button>
      ) : null}
    </div>
  );
}

export function Modal({ title, onClose, children, footer }: { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  return (
    <div
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal">
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="modal-close" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer ? <div className="modal-foot">{footer}</div> : null}
      </div>
    </div>
  );
}

export function KV({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="kv">
      <span className="k">{k}</span>
      <span className="v">{v}</span>
    </div>
  );
}

export function TimeText({ iso }: { iso?: string | null }) {
  return <span className="muted">{fmtTime(iso)}</span>;
}
