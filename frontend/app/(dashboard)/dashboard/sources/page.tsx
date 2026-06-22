"use client";
import { useState } from "react";
import { toast } from "sonner";
import { useApi, useApiQuery } from "@/lib/api";
import { useStudioStore } from "@/lib/store";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import type { Source } from "@/types/api";

const KINDS = ["topic", "url", "blog", "product", "youtube", "competitor", "pdf", "voice"] as const;

export default function SourcesPage() {
  const { activeBrandId } = useStudioStore();
  const api = useApi();
  const { data: sources = [], refetch } = useApiQuery<Source[]>(
    ["sources", activeBrandId],
    `/sources/brand/${activeBrandId}`,
    { enabled: !!activeBrandId }
  );

  const [kind, setKind] = useState<typeof KINDS[number]>("topic");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);

  async function submit() {
    if (!activeBrandId) return;
    try {
      let storage_key: string | undefined;
      if ((kind === "pdf" || kind === "voice") && file) {
        const init = await api.post<{ storage_key: string; upload_url: string }>(
          "/sources/upload-intent",
          { brand_id: activeBrandId, kind, filename: file.name, content_type: file.type || "application/octet-stream" }
        );
        const put = await fetch(init.upload_url, { method: "PUT", body: file, headers: { "Content-Type": file.type } });
        if (!put.ok) throw new Error("upload failed");
        storage_key = init.storage_key;
      }
      await api.post("/sources", {
        brand_id: activeBrandId,
        kind,
        url: url || undefined,
        raw_text: text || undefined,
        storage_key,
      });
      toast.success("Source queued for ingest");
      setUrl(""); setText(""); setFile(null);
      refetch();
    } catch (err) { toast.error((err as Error).message); }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Sources</h1>
        <p className="text-sm text-muted-foreground">Feed brand memory — every source becomes RAG context.</p>
      </div>
      <Card>
        <CardHeader><CardTitle>Add a source</CardTitle><CardDescription>Topic, URL, YouTube link, PDF, voice note, or competitor URL.</CardDescription></CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1.5">
            <Label>Kind</Label>
            <Select value={kind} onValueChange={(v) => setKind(v as any)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>{KINDS.map((k) => (<SelectItem key={k} value={k}>{k}</SelectItem>))}</SelectContent>
            </Select>
          </div>
          {(kind === "url" || kind === "blog" || kind === "product" || kind === "youtube" || kind === "competitor") && (
            <div className="space-y-1.5"><Label>URL</Label><Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" /></div>
          )}
          {kind === "topic" && (
            <div className="space-y-1.5 md:col-span-2"><Label>Seed text</Label><Textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} /></div>
          )}
          {(kind === "pdf" || kind === "voice") && (
            <div className="space-y-1.5 md:col-span-2">
              <Label>File</Label>
              <Input type="file" accept={kind === "pdf" ? "application/pdf" : "audio/*"} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </div>
          )}
        </CardContent>
        <CardFooter className="justify-end"><Button onClick={submit}>Ingest source</Button></CardFooter>
      </Card>

      <div className="space-y-2">
        {sources.map((s) => (
          <Card key={s.id}>
            <CardContent className="flex items-center justify-between py-3">
              <div className="flex items-center gap-3">
                <Badge variant="secondary">{s.kind}</Badge>
                <span className="font-medium">{s.title || s.url || s.id}</span>
              </div>
              <Badge variant={s.status === "embedded" ? "success" : s.status === "failed" ? "destructive" : "warning"}>{s.status}</Badge>
            </CardContent>
          </Card>
        ))}
        {sources.length === 0 && (<div className="text-sm text-muted-foreground">No sources yet.</div>)}
      </div>
    </div>
  );
}
