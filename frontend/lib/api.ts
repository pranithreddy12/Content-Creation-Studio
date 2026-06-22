"use client";
import { useAuth } from "@clerk/nextjs";
import { useQuery, useMutation, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";
import { toast } from "sonner";

const RAW_BASE = process.env.NEXT_PUBLIC_API_URL || "/api/studio";
// Direct backend URLs need /v1; the /api/studio rewrite in next.config.ts adds it automatically.
const BASE = RAW_BASE.startsWith("/api/studio") ? RAW_BASE : `${RAW_BASE.replace(/\/$/, "")}/v1`;

async function call<T>(path: string, opts: RequestInit & { token?: string | null } = {}): Promise<T> {
  const { token, headers, ...rest } = opts;
  const r = await fetch(`${BASE}${path.startsWith("/") ? path : "/" + path}`, {
    ...rest,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(headers as Record<string, string>),
    },
  });
  if (!r.ok) {
    const txt = await r.text().catch(() => r.statusText);
    throw new Error(`${r.status}: ${txt}`);
  }
  if (r.status === 204) return undefined as unknown as T;
  return r.json();
}

export function useApi() {
  const { getToken } = useAuth();
  return useMemo(() => ({
    get: async <T,>(path: string) => call<T>(path, { method: "GET", token: await getToken() }),
    post: async <T,>(path: string, body?: unknown) =>
      call<T>(path, { method: "POST", token: await getToken(), body: body ? JSON.stringify(body) : undefined }),
    patch: async <T,>(path: string, body?: unknown) =>
      call<T>(path, { method: "PATCH", token: await getToken(), body: body ? JSON.stringify(body) : undefined }),
    del: async <T,>(path: string) => call<T>(path, { method: "DELETE", token: await getToken() }),
  }), [getToken]);
}

export function useApiQuery<T>(key: readonly unknown[], path: string, options?: Partial<UseQueryOptions<T>> & { toastOnError?: boolean }) {
  const api = useApi();
  const { toastOnError = true, ...rest } = options ?? {};
  const q = useQuery<T>({ queryKey: key, queryFn: () => api.get<T>(path), ...rest });
  useEffect(() => {
    if (toastOnError && q.isError) {
      toast.error(`${path} → ${(q.error as Error)?.message ?? "failed"}`);
    }
  }, [q.isError, q.error, path, toastOnError]);
  return q;
}

export function useApiMutation<TIn, TOut>(
  path: string | ((vars: TIn) => string),
  method: "post" | "patch" | "del" = "post",
  invalidates: readonly unknown[][] = [],
) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation<TOut, Error, TIn>({
    mutationFn: async (vars: TIn) => {
      const p = typeof path === "function" ? path(vars) : path;
      return method === "del" ? api.del<TOut>(p) : (api as any)[method](p, vars);
    },
    onSuccess: () => { invalidates.forEach((k) => qc.invalidateQueries({ queryKey: k })); },
    onError: (err) => { toast.error(err.message); },
  });
}
