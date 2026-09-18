// ============================================================
// 认证上下文（决策 2 不变：认证层 = V1 session，V2 不重造登录）
// /api/me 获取身份 + CSRF；未登录由服务端 /v2 路由 302 + 前端兜底双保险。
// ============================================================

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { getMe, setCsrfToken } from "../api/client";

interface AuthState {
  username: string;
  ready: boolean;
}

const AuthCtx = createContext<AuthState>({ username: "", ready: false });

export function useAuth() {
  return useContext(AuthCtx);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ username: "", ready: false });
  useEffect(() => {
    getMe()
      .then((d) => {
        if (!d.logged_in) {
          window.location.href = "/";
          return;
        }
        if (d.csrf_token) setCsrfToken(d.csrf_token);
        setState({ username: d.user?.username || "", ready: true });
      })
      .catch(() => {
        window.location.href = "/";
      });
  }, []);
  return <AuthCtx.Provider value={state}>{children}</AuthCtx.Provider>;
}
