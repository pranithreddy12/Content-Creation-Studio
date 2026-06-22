import { useAuth } from "@clerk/clerk-expo";
import { useQuery, useMutation, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";
import { useMemo } from "react";
import Constants from "expo-constants";

const BASE = (Constants.expoConfig?.extra?.apiBase as string)
  || process.env.EXPO_PUBLIC_API_URL
  || "http://localhost:8000";

async function call<T>(path: string, opts: RequestInit & { token?: string | null } = {}): Promise<T> {
  const { token, headers, ...rest } = opts;
  const r = await fetch(`${BASE}/v1${path.startsWith("/") ? path : "/" + path}`, {
    ...rest,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(headers as Record<string, string>),
    },
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text().catch(() => r.statusText)}`);
  if (r.status === 204) return undefined as unknown as T;
  return r.json();
}

export function useApi() {
  const { getToken } = useAuth();
  return useMemo(() => ({
    get:    async <T,>(p: string) => call<T>(p, { method: "GET", token: await getToken() }),
    post:   async <T,>(p: string, b?: unknown) => call<T>(p, { method: "POST", token: await getToken(), body: b ? JSON.stringify(b) : undefined }),
    patch:  async <T,>(p: string, b?: unknown) => call<T>(p, { method: "PATCH", token: await getToken(), body: b ? JSON.stringify(b) : undefined }),
    del:    async <T,>(p: string) => call<T>(p, { method: "DELETE", token: await getToken() }),
  }), [getToken]);
}

export function useApiQuery<T>(key: readonly unknown[], path: string, options?: Partial<UseQueryOptions<T>>) {
  const api = useApi();
  return useQuery<T>({ queryKey: key, queryFn: () => api.get<T>(path), ...options });
}
