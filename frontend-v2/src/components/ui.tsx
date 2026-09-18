// ============================================================
// 通用展示组件（本轮设计系统的最小集合）
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

export function ProvBadge({ prov }: { prov?: string }) {
  const p = (prov || "").toLowerCase();
  const cls = p === "llm" ? "prov-llm" : p === "strategy" ? "prov-strategy" : "prov-other";
  return <span className={`prov ${cls}`}>{(prov || "-").toUpperCase()}</span>;
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

export function MetricCard({ label, value, sub, accent }: { label: string; value: ReactNode; sub?: string; accent?: boolean }) {
  return (
    <div className={`metric${accent ? " accent" : ""}`}>
      <div className="m-label">{label}</div>
      <div className="m-value">{value}</div>
      {sub ? <div className="m-sub">{sub}</div> : null}
    </div>
  );
}

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
