import { UserButton } from "@clerk/nextjs";
import { Sidebar } from "@/components/dashboard/sidebar";
import { BrandSwitcher } from "@/components/dashboard/brand-switcher";

// Authed, per-user pages — render at request time. Avoids build-time static
// prerender of Clerk components (which require ClerkProvider + a runtime key).
export const dynamic = "force-dynamic";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen w-full grid-cols-[260px_1fr]">
      <Sidebar />
      <div className="flex flex-col">
        <header className="flex h-14 items-center justify-between border-b px-6">
          <BrandSwitcher />
          <UserButton />
        </header>
        <main className="flex-1 overflow-auto p-6">{children}</main>
      </div>
    </div>
  );
}
