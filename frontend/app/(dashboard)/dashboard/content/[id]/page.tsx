"use client";
import { useParams } from "next/navigation";
import { useApiQuery } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { ContentAsset } from "@/types/api";

export default function AssetDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: asset, isLoading, isError, error } = useApiQuery<ContentAsset>(["asset", id], `/assets/${id}`);
  if (isLoading) return <div className="text-sm text-muted-foreground">Loading…</div>;
  if (isError) return <div className="text-sm text-destructive">Couldn't load asset: {(error as Error)?.message}</div>;
  if (!asset) return <div className="text-sm text-muted-foreground">Not found.</div>;
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{asset.title || "Untitled"}</h1>
          <p className="text-xs text-muted-foreground">{asset.format} • {new Date(asset.created_at).toLocaleString()}</p>
        </div>
        <Badge variant={asset.status === "published" ? "success" : "outline"}>{asset.status}</Badge>
      </div>
      <Card>
        <CardHeader><CardTitle className="text-base">Body</CardTitle></CardHeader>
        <CardContent>
          <pre className="whitespace-pre-wrap text-sm leading-6">{asset.body}</pre>
        </CardContent>
      </Card>
      {asset.body_json != null && (
        <Card>
          <CardHeader><CardTitle className="text-base">Structured payload</CardTitle></CardHeader>
          <CardContent><pre className="text-xs">{JSON.stringify(asset.body_json, null, 2)}</pre></CardContent>
        </Card>
      )}
    </div>
  );
}
