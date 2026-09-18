// ============================================================
// V2 前端 API 基础封装（Step 11 UI 重构）
//
// 认证沿用决策2：不重造登录。/api/me 取身份 + CSRF token；
// 所有受保护请求经 authFetch()，401 → 跳回 V1 "/"（与旧版行为一致）。
// ============================================================

let csrfToken = "";

export function setCsrfToken(t: string) {
  csrfToken = t;
}

export interface MeInfo {
  logged_in: boolean;
  csrf_token?: string;
  user?: { username: string };
}

export async function getMe(): Promise<MeInfo> {
  const r = await fetch("/api/me");
  return (await r.json()) as MeInfo;
}

/** 受保护 fetch：自动带 X-CSRF-Token；401 跳回 V1 登录。 */
export async function authFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
  const r = await fetch(url, { ...init, headers });
  if (r.status === 401) {
    window.location.href = "/";
    throw new Error("登录已过期");
  }
  return r;
}

/** JSON 请求助手：非 2xx 时抛后端 error 文案（供 UI 统一展示）。 */
export async function apiJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const r = await authFetch(url, init);
  let body: unknown = null;
  try {
    body = await r.json();
  } catch {
    /* 空响应体 */
  }
  if (!r.ok) {
    const msg = (body as { error?: string })?.error || `请求失败 (${r.status})`;
    throw new ApiError(msg, r.status, body);
  }
  return body as T;
}

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(msg: string, status: number, body: unknown) {
    super(msg);
    this.status = status;
    this.body = body;
  }
}

export function jsonInit(method: string, payload: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}
