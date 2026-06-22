"use client";
import { useEffect } from "react";
import { ChevronsUpDown, Plus } from "lucide-react";
import Link from "next/link";
import { useApiQuery } from "@/lib/api";
import { useStudioStore } from "@/lib/store";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import type { Brand } from "@/types/api";

export function BrandSwitcher() {
  const { data: brands = [], isLoading } = useApiQuery<Brand[]>(["brands"], "/brands");
  const { activeBrandId, setActiveBrand } = useStudioStore();

  useEffect(() => {
    if (!activeBrandId && brands.length > 0) setActiveBrand(brands[0].id);
  }, [brands, activeBrandId, setActiveBrand]);

  if (isLoading) return <div className="h-8 w-48 animate-pulse rounded bg-muted" />;
  if (brands.length === 0) {
    return (
      <Link href="/dashboard/brands/new">
        <Button size="sm" variant="outline"><Plus className="mr-2 h-4 w-4" />Create brand</Button>
      </Link>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <Select value={activeBrandId ?? undefined} onValueChange={(v) => setActiveBrand(v)}>
        <SelectTrigger className="w-[260px]">
          <SelectValue placeholder="Select brand" />
        </SelectTrigger>
        <SelectContent>
          {brands.map((b) => (<SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>))}
        </SelectContent>
      </Select>
      <Link href="/dashboard/brands/new">
        <Button size="icon" variant="ghost" aria-label="new brand"><Plus className="h-4 w-4" /></Button>
      </Link>
    </div>
  );
}
