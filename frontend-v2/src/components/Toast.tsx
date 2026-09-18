// ============================================================
// Toast：全局轻量提示（成功/警告/错误），自动消失
// ============================================================

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

type ToastKind = "success" | "warn" | "error";
interface ToastItem {
  id: number;
  kind: ToastKind;
  msg: string;
}

const ToastCtx = createContext<(msg: string, kind?: ToastKind) => void>(() => {});

export function useToast() {
  return useContext(ToastCtx);
}

let nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((msg: string, kind: ToastKind = "success") => {
    const id = nextId++;
    setItems((xs) => [...xs, { id, kind, msg }]);
    setTimeout(() => setItems((xs) => xs.filter((t) => t.id !== id)), 3600);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind === "success" ? "" : t.kind}`}>
            {t.kind === "success" ? "OK" : "!"} {t.msg}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
