"use client";
import { useState } from "react";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useApi } from "@/lib/api";

export interface PlatformDef { platform: string; label: string }

type FieldKind = "text" | "password" | "url" | "email" | "number";
interface Field {
  name: string;
  label: string;
  kind?: FieldKind;
  placeholder?: string;
  helper?: string;
}

interface OauthBYOPlatform {
  type: "oauth-byo";
  docs?: string;
}
interface CredsPlatform {
  type: "wordpress" | "email";
  fields: Field[];
  endpoint: string;
  buildBody: (form: Record<string, string>, brandId: string) => Record<string, unknown>;
}

type PlatformSpec = OauthBYOPlatform | CredsPlatform;

const PLATFORM_SPECS: Record<string, PlatformSpec> = {
  linkedin:  { type: "oauth-byo", docs: "https://www.linkedin.com/developers/apps" },
  x:         { type: "oauth-byo", docs: "https://developer.twitter.com/en/portal/dashboard" },
  facebook:  { type: "oauth-byo", docs: "https://developers.facebook.com/apps" },
  instagram: { type: "oauth-byo", docs: "https://developers.facebook.com/apps" },
  tiktok:    { type: "oauth-byo", docs: "https://developers.tiktok.com/apps" },
  youtube:   { type: "oauth-byo", docs: "https://console.cloud.google.com/apis/credentials" },
  reddit:    { type: "oauth-byo", docs: "https://www.reddit.com/prefs/apps" },
  wordpress: {
    type: "wordpress",
    endpoint: "/publishing/wordpress",
    fields: [
      { name: "site",         label: "Site URL",       kind: "url",      placeholder: "https://yourblog.com" },
      { name: "username",     label: "Username",                          placeholder: "wp username" },
      { name: "app_password", label: "Application password", kind: "password", helper: "Profile → Users → Application Passwords" },
      { name: "display_name", label: "Display name (optional)" },
    ],
    buildBody: (f, brandId) => ({
      brand_id: brandId,
      site: f.site,
      username: f.username,
      app_password: f.app_password,
      display_name: f.display_name || undefined,
    }),
  },
  email: {
    type: "email",
    endpoint: "/publishing/email",
    fields: [
      { name: "api_key",      label: "Brevo API key", kind: "password" },
      { name: "sender_name",  label: "Sender name",   placeholder: "Studio" },
      { name: "sender_email", label: "Sender email",  kind: "email", placeholder: "you@brand.com" },
      { name: "list_ids",     label: "List IDs (comma-separated)", placeholder: "1, 2, 3" },
    ],
    buildBody: (f, brandId) => ({
      brand_id: brandId,
      api_key: f.api_key,
      sender_name: f.sender_name,
      sender_email: f.sender_email,
      list_ids: (f.list_ids || "").split(",").map((s) => s.trim()).filter(Boolean).map(Number),
    }),
  },
};


export function ConnectDialog({
  platform, label, brandId, open, onOpenChange, onSuccess,
}: {
  platform: string;
  label: string;
  brandId: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSuccess: () => void;
}) {
  const api = useApi();
  const spec = PLATFORM_SPECS[platform];
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});

  if (!spec) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent>
          <DialogHeader><DialogTitle>{label}</DialogTitle></DialogHeader>
          <p className="text-sm text-muted-foreground">No connection method defined for this platform yet.</p>
        </DialogContent>
      </Dialog>
    );
  }

  function update(k: string, v: string) { setForm((f) => ({ ...f, [k]: v })); }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      if (spec.type === "oauth-byo") {
        const redirect = `${location.origin}/dashboard/channels/callback`;
        const res = await api.post<{ url: string }>("/publishing/oauth/start", {
          platform,
          brand_id: brandId,
          redirect_uri: redirect,
          client_id: form.client_id,
          client_secret: form.client_secret,
        });
        location.assign(res.url);
        return;
      }
      await api.post(spec.endpoint, spec.buildBody(form, brandId));
      toast.success(`Connected ${label}`);
      onSuccess();
      onOpenChange(false);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  const fields: Field[] = spec.type === "oauth-byo"
    ? [
        { name: "client_id",     label: "Client ID",     placeholder: "from your platform app" },
        { name: "client_secret", label: "Client Secret", kind: "password" },
      ]
    : spec.fields;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Connect {label}</DialogTitle>
          <DialogDescription>
            {spec.type === "oauth-byo" ? (
              <>Create an OAuth app{spec.docs ? <> at <a href={spec.docs} target="_blank" className="underline">{spec.docs}</a></> : ""}, paste its Client ID and Secret here, and we'll open the consent screen.</>
            ) : (
              <>Provide credentials to connect this channel.</>
            )}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-3">
          {fields.map((f) => (
            <div key={f.name} className="space-y-1.5">
              <Label>{f.label}</Label>
              <Input
                type={f.kind ?? "text"}
                value={form[f.name] ?? ""}
                onChange={(e) => update(f.name, e.target.value)}
                placeholder={f.placeholder}
                required={f.name !== "display_name" && f.name !== "list_ids"}
              />
              {f.helper && <p className="text-xs text-muted-foreground">{f.helper}</p>}
            </div>
          ))}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? "Connecting…" : spec.type === "oauth-byo" ? "Continue to OAuth" : "Connect"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
