"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Calendar, CheckSquare, BarChart3, Settings, Sparkles, Workflow, FolderKanban, Boxes, FileText, Library } from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/dashboard",            label: "Overview",     icon: Sparkles },
  { href: "/dashboard/calendar",   label: "Calendar",     icon: Calendar },
  { href: "/dashboard/approvals",  label: "Approvals",    icon: CheckSquare },
  { href: "/dashboard/content",    label: "Content",      icon: FileText },
  { href: "/dashboard/sources",    label: "Sources",      icon: Library },
  { href: "/dashboard/workflows",  label: "Workflows",    icon: Workflow },
  { href: "/dashboard/analytics",  label: "Analytics",    icon: BarChart3 },
  { href: "/dashboard/brands",     label: "Brands",       icon: Boxes },
  { href: "/dashboard/channels",   label: "Channels",     icon: FolderKanban },
  { href: "/dashboard/settings",   label: "Settings",     icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="flex flex-col gap-1 border-r bg-muted/40 px-3 py-6">
      <div className="mb-6 flex items-center gap-2 px-3 text-lg font-semibold">
        <Sparkles className="h-5 w-5" /> Studio
      </div>
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = pathname === href || (href !== "/dashboard" && pathname.startsWith(href));
        return (
          <Link key={href} href={href} className={cn(
            "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
            active ? "bg-primary text-primary-foreground" : "hover:bg-accent hover:text-accent-foreground"
          )}>
            <Icon className="h-4 w-4" /> {label}
          </Link>
        );
      })}
    </aside>
  );
}
