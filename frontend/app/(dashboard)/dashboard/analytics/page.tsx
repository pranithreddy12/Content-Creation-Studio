"use client";
import { useApiQuery } from "@/lib/api";
import { useStudioStore } from "@/lib/store";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { AssetMetricRow } from "@/types/api";

export default function AnalyticsPage() {
  const { activeBrandId } = useStudioStore();
  const { data = [] } = useApiQuery<AssetMetricRow[]>(
    ["analytics", activeBrandId],
    `/analytics/timeseries?brand_id=${activeBrandId}&window=30d`,
    { enabled: !!activeBrandId }
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Analytics</h1>
        <p className="text-sm text-muted-foreground">Per-channel performance, 30-day rolling.</p>
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Views by platform</CardTitle></CardHeader>
          <CardContent className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                <XAxis dataKey="platform" stroke="hsl(var(--muted-foreground))" />
                <YAxis stroke="hsl(var(--muted-foreground))" />
                <Tooltip contentStyle={{ backgroundColor: "hsl(var(--popover))", borderRadius: "6px", border: "1px solid hsl(var(--border))" }} />
                <Bar dataKey="views" fill="hsl(var(--primary))" />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Engagement trend</CardTitle></CardHeader>
          <CardContent className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                <XAxis dataKey="collected_at" stroke="hsl(var(--muted-foreground))" />
                <YAxis stroke="hsl(var(--muted-foreground))" />
                <Tooltip contentStyle={{ backgroundColor: "hsl(var(--popover))", borderRadius: "6px", border: "1px solid hsl(var(--border))" }} />
                <Legend />
                <Line type="monotone" dataKey="likes" stroke="hsl(var(--primary))" />
                <Line type="monotone" dataKey="shares" stroke="#10b981" />
                <Line type="monotone" dataKey="clicks" stroke="#f59e0b" />
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
