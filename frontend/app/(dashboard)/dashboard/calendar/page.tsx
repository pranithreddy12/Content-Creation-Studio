"use client";
import { useMemo, useState } from "react";
import { addDays, format, startOfMonth, eachDayOfInterval, endOfMonth, isSameDay, parseISO } from "date-fns";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useApiQuery } from "@/lib/api";
import { useStudioStore } from "@/lib/store";
import { cn } from "@/lib/utils";
import type { Schedule } from "@/types/api";

const FORMAT_COLORS: Record<string, string> = {
  blog: "bg-sky-500/15 text-sky-700",
  linkedin: "bg-blue-500/15 text-blue-700",
  x_thread: "bg-zinc-500/15 text-zinc-700",
  instagram: "bg-pink-500/15 text-pink-700",
  reel: "bg-fuchsia-500/15 text-fuchsia-700",
  short: "bg-rose-500/15 text-rose-700",
  tiktok: "bg-emerald-500/15 text-emerald-700",
};

export default function CalendarPage() {
  const { activeBrandId } = useStudioStore();
  const [cursor, setCursor] = useState(new Date());
  const monthStart = useMemo(() => startOfMonth(cursor), [cursor]);
  const monthEnd = useMemo(() => endOfMonth(cursor), [cursor]);
  const days = useMemo(() => eachDayOfInterval({ start: monthStart, end: monthEnd }), [monthStart, monthEnd]);

  const { data: schedules = [] } = useApiQuery<Schedule[]>(
    ["schedules", activeBrandId, format(monthStart, "yyyy-MM")],
    `/calendar?brand_id=${activeBrandId}&from=${monthStart.toISOString()}&to=${monthEnd.toISOString()}`,
    { enabled: !!activeBrandId }
  );

  const startDow = monthStart.getDay();
  const blanks = Array.from({ length: startDow });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Calendar</h1>
          <p className="text-sm text-muted-foreground">Scheduled & published assets across all channels.</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="icon" onClick={() => setCursor(addDays(monthStart, -1))}><ChevronLeft className="h-4 w-4" /></Button>
          <div className="min-w-[140px] text-center font-medium">{format(cursor, "LLLL yyyy")}</div>
          <Button variant="ghost" size="icon" onClick={() => setCursor(addDays(monthEnd, 1))}><ChevronRight className="h-4 w-4" /></Button>
        </div>
      </div>
      <Card>
        <CardContent className="p-0">
          <div className="grid grid-cols-7 border-b text-xs font-medium text-muted-foreground">
            {["Sun","Mon","Tue","Wed","Thu","Fri","Sat"].map((d) => (<div key={d} className="border-r p-3 last:border-r-0">{d}</div>))}
          </div>
          <div className="grid grid-cols-7">
            {blanks.map((_, i) => (<div key={`b${i}`} className="min-h-[120px] border-b border-r" />))}
            {days.map((d) => {
              const onDay = schedules.filter((s) => isSameDay(parseISO(s.scheduled_at), d));
              return (
                <div key={d.toISOString()} className="min-h-[120px] border-b border-r p-2">
                  <div className="text-xs font-medium text-muted-foreground">{format(d, "d")}</div>
                  <div className="mt-1 flex flex-col gap-1">
                    {onDay.slice(0, 4).map((s) => (
                      <Badge key={s.id} variant="outline" className={cn("max-w-full truncate", FORMAT_COLORS[(s as any).format] ?? "")}>
                        {(s as any).format ?? "asset"}
                      </Badge>
                    ))}
                    {onDay.length > 4 && (<div className="text-[10px] text-muted-foreground">+{onDay.length - 4} more</div>)}
                  </div>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
