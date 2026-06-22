"use client";
import { useState } from "react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { useApi, useApiQuery } from "@/lib/api";
import { useStudioStore } from "@/lib/store";
import type { ContentAsset } from "@/types/api";
import { Check, X, Eye } from "lucide-react";

export default function ApprovalsPage() {
  const { activeBrandId } = useStudioStore();
  const [tab, setTab] = useState<"draft" | "review" | "scheduled">("review");
  const api = useApi();
  const { data: assets = [], refetch } = useApiQuery<ContentAsset[]>(
    ["assets", activeBrandId, tab],
    `/assets?brand_id=${activeBrandId}&status=${tab}`,
    { enabled: !!activeBrandId }
  );

  async function act(asset: ContentAsset, action: "approve" | "reject" | "schedule") {
    try {
      await api.post(`/assets/${asset.id}/${action}`);
      toast.success(`Asset ${action}d`);
      refetch();
    } catch (err) {
      toast.error((err as Error).message);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Approvals</h1>
        <p className="text-sm text-muted-foreground">Review every asset before it goes live.</p>
      </div>
      <Tabs value={tab} onValueChange={(v) => setTab(v as any)}>
        <TabsList>
          <TabsTrigger value="draft">Drafts</TabsTrigger>
          <TabsTrigger value="review">In review</TabsTrigger>
          <TabsTrigger value="scheduled">Scheduled</TabsTrigger>
        </TabsList>
        <TabsContent value={tab} className="grid gap-4 md:grid-cols-2">
          {assets.map((a) => (
            <Card key={a.id}>
              <CardHeader>
                <div className="flex items-center justify-between gap-2">
                  <CardTitle className="text-base">{a.title || "Untitled"}</CardTitle>
                  <Badge variant="secondary">{a.format}</Badge>
                </div>
                <CardDescription>{a.word_count ?? 0} words • {new Date(a.created_at).toLocaleString()}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="line-clamp-4 text-sm text-muted-foreground whitespace-pre-wrap">{a.body?.slice(0, 400)}</p>
                <div className="flex items-center gap-2">
                  <Button size="sm" variant="outline" onClick={() => location.assign(`/dashboard/content/${a.id}`)}><Eye className="mr-1 h-3 w-3" />Open</Button>
                  <Button size="sm" onClick={() => act(a, "approve")}><Check className="mr-1 h-3 w-3" />Approve</Button>
                  <Button size="sm" variant="destructive" onClick={() => act(a, "reject")}><X className="mr-1 h-3 w-3" />Reject</Button>
                </div>
              </CardContent>
            </Card>
          ))}
          {assets.length === 0 && (<div className="col-span-full text-sm text-muted-foreground">Nothing here yet.</div>)}
        </TabsContent>
      </Tabs>
    </div>
  );
}
