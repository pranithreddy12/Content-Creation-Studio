"use client";
import Link from "next/link";
import { useApiQuery } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Plus } from "lucide-react";
import type { Brand } from "@/types/api";

export default function BrandsPage() {
  const { data = [] } = useApiQuery<Brand[]>(["brands"], "/brands");
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Brands</h1>
          <p className="text-sm text-muted-foreground">Each brand has its own memory, schedule and channels.</p>
        </div>
        <Link href="/dashboard/brands/new"><Button><Plus className="mr-1 h-4 w-4" />New brand</Button></Link>
      </div>
      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
        {data.map((b) => (
          <Card key={b.id}>
            <CardHeader><CardTitle className="text-base">{b.name}</CardTitle></CardHeader>
            <CardContent className="space-y-1 text-xs text-muted-foreground">
              <div>{b.description || "—"}</div>
              <div>{b.primary_topic} · {b.timezone} · quota {b.daily_quota}</div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
