"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

type ToastKind = "success" | "error" | "info";

type Toast = {
  id: number;
  kind: ToastKind;
  message: string;
  leaving?: boolean;
};

type ToastContextValue = {
  toast: (kind: ToastKind, message: string) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx.toast;
}

const KIND_STYLES: Record<ToastKind, { bg: string; border: string; icon: string }> = {
  success: { bg: "bg-emerald-50", border: "border-emerald-300", icon: "✓" },
  error: { bg: "bg-red-50", border: "border-red-300", icon: "✕" },
  info: { bg: "bg-sky-50", border: "border-sky-300", icon: "ℹ" },
};

const AUTO_DISMISS_MS = 5000;
const LEAVE_MS = 200;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(0);

  const dismiss = useCallback((id: number) => {
    // two-phase: mark leaving for the exit transition, then remove
    setToasts((t) => t.map((x) => (x.id === id ? { ...x, leaving: true } : x)));
    setTimeout(
      () => setToasts((t) => t.filter((x) => x.id !== id)),
      LEAVE_MS
    );
  }, []);

  const toast = useCallback(
    (kind: ToastKind, message: string) => {
      const id = nextId.current++;
      setToasts((t) => [...t.slice(-3), { id, kind, message }]); // cap at 4 visible
      setTimeout(() => dismiss(id), AUTO_DISMISS_MS);
    },
    [dismiss]
  );

  const value = useMemo(() => ({ toast }), [toast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        aria-atomic="false"
        className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-full max-w-sm flex-col gap-2"
      >
        {toasts.map((t) => {
          const s = KIND_STYLES[t.kind];
          return (
            <div
              key={t.id}
              role="status"
              className={`pointer-events-auto flex items-start gap-2.5 rounded-lg border ${s.border} ${s.bg} px-3.5 py-2.5 shadow-md transition-all duration-200 motion-reduce:transition-none ${
                t.leaving ? "translate-y-1 opacity-0" : "translate-y-0 opacity-100"
              }`}
            >
              <span aria-hidden className="mt-0.5 text-xs font-bold text-neutral-500">
                {s.icon}
              </span>
              <span className="flex-1 text-sm text-neutral-800">{t.message}</span>
              <button
                type="button"
                onClick={() => dismiss(t.id)}
                aria-label="Dismiss notification"
                className="rounded p-0.5 text-neutral-400 hover:text-neutral-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-neutral-400"
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
                  <path d="M3.5 3.5l7 7m0-7l-7 7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}
