"use client";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useApiQuery } from "@/lib/api";
import { useStudioStore } from "@/lib/store";
import { Activity, CheckCircle2, Clock, FileText, ArrowRight } from "lucide-react";
import { formatNumber } from "@/lib/utils";
import { toast } from "sonner";
import { useEffect } from "react";

interface OverviewStats {
  generated: number;
  scheduled: number;
  published: number;
  avg_viral_score: number;
  avg_seo_score: number;
  revenue_attributed: number;
}

export default function OverviewPage() {
  const { activeBrandId } = useStudioStore();
  const { data, isError, error, isLoading } = useApiQuery<OverviewStats>(
    ["overview", activeBrandId],
    `/analytics/overview${activeBrandId ? `?brand_id=${activeBrandId}` : ""}`,
    { enabled: !!activeBrandId }
  );

  useEffect(() => {
    if (isError) toast.error(`Overview failed to load: ${(error as Error)?.message ?? "unknown"}`);
  }, [isError, error]);

  if (!activeBrandId) {
    return (
      <div className="mx-auto max-w-xl">
        <Card>
          <CardHeader>
            <CardTitle>Welcome to Studio</CardTitle>
            <CardDescription>You don't have any brands yet. Create one to start generating content.</CardDescription>
          </CardHeader>
          <CardContent>
            <Link href="/dashboard/brands/new">
              <Button>Create your first brand <ArrowRight className="ml-2 h-4 w-4" /></Button>
            </Link>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Overview</h1>
        <p className="text-sm text-muted-foreground">Today's content engine snapshot.</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Stat icon={FileText} label="Generated" value={isLoading ? "…" : formatNumber(data?.generated)} />
        <Stat icon={Clock} label="Scheduled" value={isLoading ? "…" : formatNumber(data?.scheduled)} />
        <Stat icon={CheckCircle2} label="Published" value={isLoading ? "…" : formatNumber(data?.published)} />
        <Stat icon={Activity} label="Avg viral score" value={isLoading ? "…" : (data?.avg_viral_score?.toFixed(2) ?? "—")} />
      </div>
      {isError && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Couldn't load overview. {(error as Error)?.message}
        </div>
      )}
    </div>
  );
}

function Stat({ icon: Icon, label, value }: { icon: any; label: string; value: string }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{label}</CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
      </CardContent>
    </Card>
  );
}
