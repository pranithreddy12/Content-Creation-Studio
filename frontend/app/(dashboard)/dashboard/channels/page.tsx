"use client";
import { useState } from "react";
import { toast } from "sonner";
import { useApi, useApiQuery } from "@/lib/api";
import { useStudioStore } from "@/lib/store";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ConnectDialog } from "@/components/channels/connect-dialog";
import type { Channel } from "@/types/api";

const SUPPORTED = [
  { platform: "linkedin",  label: "LinkedIn" },
  { platform: "x",         label: "X (Twitter)" },
  { platform: "facebook",  label: "Facebook" },
  { platform: "instagram", label: "Instagram" },
  { platform: "tiktok",    label: "TikTok" },
  { platform: "youtube",   label: "YouTube" },
  { platform: "reddit",    label: "Reddit" },
  { platform: "wordpress", label: "WordPress" },
  { platform: "email",     label: "Email (Brevo)" },
];

export default function ChannelsPage() {
  const { activeBrandId } = useStudioStore();
  const api = useApi();
  const { data: channels = [], refetch } = useApiQuery<Channel[]>(
    ["channels", activeBrandId],
    `/publishing/channels/${activeBrandId}`,
    { enabled: !!activeBrandId }
  );
  const [pending, setPending] = useState<{ platform: string; label: string } | null>(null);

  function openConnect(platform: string, label: string) {
    if (!activeBrandId) { toast.error("Pick or create a brand first"); return; }
    setPending({ platform, label });
  }

  async function disconnect(id: string) {
    try {
      await api.del(`/publishing/channels/${id}`);
      toast.success("Disconnected");
      refetch();
    } catch (err) {
      toast.error((err as Error).message);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Channels</h1>
        <p className="text-sm text-muted-foreground">Connect every platform Studio should auto-publish to.</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {SUPPORTED.map((p) => {
          const live = channels.find((c) => c.platform === p.platform && c.status === "connected");
          return (
            <Card key={p.platform}>
              <CardHeader><CardTitle className="text-base">{p.label}</CardTitle></CardHeader>
              <CardContent className="flex items-center justify-between">
                {live ? (
                  <>
                    <Badge variant="success">Connected · {live.display_name}</Badge>
                    <Button size="sm" variant="outline" onClick={() => disconnect(live.id)}>Disconnect</Button>
                  </>
                ) : (
                  <>
                    <Badge variant="secondary">Not connected</Badge>
                    <Button size="sm" onClick={() => openConnect(p.platform, p.label)}>Connect</Button>
                  </>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>

      {pending && activeBrandId && (
        <ConnectDialog
          platform={pending.platform}
          label={pending.label}
          brandId={activeBrandId}
          open={!!pending}
          onOpenChange={(v) => !v && setPending(null)}
          onSuccess={() => { setPending(null); refetch(); }}
        />
      )}
    </div>
  );
}
