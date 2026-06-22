"use client";
import { useApiQuery } from "@/lib/api";
import { useStudioStore } from "@/lib/store";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { ContentAsset } from "@/types/api";
import Link from "next/link";

export default function ContentPage() {
  const { activeBrandId } = useStudioStore();
  const { data = [] } = useApiQuery<ContentAsset[]>(
    ["content-all", activeBrandId],
    `/assets?brand_id=${activeBrandId}`,
    { enabled: !!activeBrandId }
  );
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Content library</h1>
        <p className="text-sm text-muted-foreground">Every asset generated for this brand.</p>
      </div>
      <div className="grid gap-3">
        {data.map((a) => (
          <Link key={a.id} href={`/dashboard/content/${a.id}`} className="block">
            <Card className="transition-colors hover:bg-accent">
              <CardContent className="flex items-center justify-between py-3">
                <div className="flex items-center gap-3">
                  <Badge variant="secondary">{a.format}</Badge>
                  <span className="font-medium">{a.title || "Untitled"}</span>
                </div>
                <Badge variant={a.status === "published" ? "success" : a.status === "failed" ? "destructive" : "outline"}>{a.status}</Badge>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
