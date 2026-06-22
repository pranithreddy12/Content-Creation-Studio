"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useApi } from "@/lib/api";
import { useStudioStore } from "@/lib/store";

const SLUG_RE = /^[a-z0-9-]+$/;

export default function NewBrandPage() {
  const api = useApi();
  const router = useRouter();
  const { setActiveBrand } = useStudioStore();
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState({
    name: "", slug: "",
    description: "", website_url: "", product_url: "",
    competitor_urls: "", primary_topic: "", audience: "",
    tone: "professional", daily_quota: 1, timezone: "UTC",
  });

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!SLUG_RE.test(form.slug)) { toast.error("Slug must be lowercase letters/numbers/-"); return; }
    setSubmitting(true);
    try {
      const body = {
        ...form,
        competitor_urls: form.competitor_urls.split(",").map((s) => s.trim()).filter(Boolean),
        daily_quota: Number(form.daily_quota),
      };
      const brand = await api.post<{ id: string }>("/brands", body);
      setActiveBrand(brand.id);
      toast.success("Brand created");
      router.push("/dashboard");
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="mx-auto max-w-2xl">
      <Card>
        <CardHeader>
          <CardTitle>Create a brand</CardTitle>
          <CardDescription>The single brain that drives every generated asset.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-2 md:grid-cols-2">
            <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></Field>
            <Field label="Slug"><Input value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value.toLowerCase() })} required placeholder="acme-co" /></Field>
          </div>
          <Field label="Description"><Textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
          <div className="grid gap-2 md:grid-cols-2">
            <Field label="Website URL"><Input type="url" value={form.website_url} onChange={(e) => setForm({ ...form, website_url: e.target.value })} /></Field>
            <Field label="Product URL"><Input type="url" value={form.product_url} onChange={(e) => setForm({ ...form, product_url: e.target.value })} /></Field>
          </div>
          <Field label="Competitor URLs (comma separated)"><Input value={form.competitor_urls} onChange={(e) => setForm({ ...form, competitor_urls: e.target.value })} /></Field>
          <div className="grid gap-2 md:grid-cols-2">
            <Field label="Primary topic"><Input value={form.primary_topic} onChange={(e) => setForm({ ...form, primary_topic: e.target.value })} /></Field>
            <Field label="Audience"><Input value={form.audience} onChange={(e) => setForm({ ...form, audience: e.target.value })} /></Field>
          </div>
          <div className="grid gap-2 md:grid-cols-3">
            <Field label="Tone">
              <Select value={form.tone} onValueChange={(v) => setForm({ ...form, tone: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {["professional", "friendly", "expert", "witty", "bold", "playful"].map((t) => (
                    <SelectItem key={t} value={t}>{t}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Daily quota"><Input type="number" min={1} max={20} value={form.daily_quota} onChange={(e) => setForm({ ...form, daily_quota: Number(e.target.value) })} /></Field>
            <Field label="Timezone"><Input value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })} placeholder="UTC" /></Field>
          </div>
        </CardContent>
        <CardFooter className="justify-end">
          <Button type="submit" disabled={submitting}>{submitting ? "Creating…" : "Create brand"}</Button>
        </CardFooter>
      </Card>
    </form>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="space-y-1.5"><Label>{label}</Label>{children}</div>;
}
