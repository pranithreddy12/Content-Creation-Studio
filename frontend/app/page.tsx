import Link from "next/link";
import { Button } from "@/components/ui/button";
import { ArrowRight, Sparkles, Zap, Repeat, Video, BarChart3, Brain } from "lucide-react";

const CLERK_ON = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

export default function HomePage() {
  return (
    <main className="min-h-screen bg-gradient-to-b from-background via-background to-muted">
      <header className="container flex h-16 items-center justify-between">
        <div className="flex items-center gap-2 font-bold">
          <Sparkles className="h-5 w-5" /> Studio
        </div>
        <div className="flex items-center gap-2">
          {CLERK_ON ? (
            <>
              <Link href="/sign-in"><Button variant="ghost">Sign in</Button></Link>
              <Link href="/sign-up"><Button>Get started</Button></Link>
            </>
          ) : (
            <Link href="/dashboard"><Button variant="outline">Open dashboard (auth disabled)</Button></Link>
          )}
        </div>
      </header>

      <section className="container py-24 text-center">
        <h1 className="mx-auto max-w-4xl text-5xl font-bold tracking-tight md:text-6xl">
          One idea → an entire omnichannel content engine
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-lg text-muted-foreground">
          Autonomous research, writing, video, SEO, and publishing across 10+ platforms. The system learns from results and improves itself daily.
        </p>
        <div className="mt-10 flex items-center justify-center gap-4">
          {CLERK_ON ? (
            <>
              <Link href="/sign-up"><Button size="lg">Start free <ArrowRight className="ml-2 h-4 w-4" /></Button></Link>
              <Link href="/sign-in"><Button size="lg" variant="outline">I already have an account</Button></Link>
            </>
          ) : (
            <div className="rounded-md border bg-card px-4 py-3 text-left text-sm">
              <div className="font-medium">Auth not configured</div>
              <div className="mt-1 text-muted-foreground">
                Add <code className="rounded bg-muted px-1">NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY</code> to <code className="rounded bg-muted px-1">.env</code> and restart to enable sign-in.
              </div>
            </div>
          )}
        </div>
      </section>

      <section className="container grid gap-6 pb-24 sm:grid-cols-2 lg:grid-cols-3">
        {[
          { i: Zap, t: "Daily autopilot", d: "Research, ideate, score, write, render, publish — every day." },
          { i: Repeat, t: "15 formats per idea", d: "Blog, X thread, LinkedIn, Reel, Short, TikTok, Email, Ad and more." },
          { i: Video, t: "Auto-generated videos", d: "Script → b-roll → TTS → captions → MP4 with one click." },
          { i: BarChart3, t: "Analytics + attribution", d: "Per-platform performance feeds the next day's prompts." },
          { i: Brain, t: "Viral pattern memory", d: "Studies viral hooks from X, LinkedIn, Reels — reuses what works." },
          { i: Sparkles, t: "Agency-ready", d: "Unlimited brands, workspaces, teams, RBAC." },
        ].map(({ i: Icon, t, d }) => (
          <div key={t} className="rounded-lg border bg-card p-6">
            <Icon className="mb-3 h-6 w-6" />
            <div className="font-semibold">{t}</div>
            <div className="mt-1 text-sm text-muted-foreground">{d}</div>
          </div>
        ))}
      </section>
    </main>
  );
}
