"use client";
import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { useApi } from "@/lib/api";

function Inner() {
  const router = useRouter();
  const sp = useSearchParams();
  const api = useApi();

  useEffect(() => {
    const platform = sp.get("platform") || "";
    const code = sp.get("code") || "";
    const state = sp.get("state") || "";
    const redirect_uri = `${location.origin}/dashboard/channels/callback`;
    if (!code || !state) { router.replace("/dashboard/channels"); return; }
    api.get(`/publishing/oauth/callback?platform=${platform}&code=${code}&state=${state}&redirect_uri=${encodeURIComponent(redirect_uri)}`)
      .then(() => { toast.success(`Connected ${platform}`); router.replace("/dashboard/channels"); })
      .catch((e) => { toast.error(e.message); router.replace("/dashboard/channels"); });
  }, [sp, router, api]);
  return <div className="p-8 text-sm text-muted-foreground">Connecting…</div>;
}

export default function CallbackPage() {
  return <Suspense fallback={null}><Inner /></Suspense>;
}
