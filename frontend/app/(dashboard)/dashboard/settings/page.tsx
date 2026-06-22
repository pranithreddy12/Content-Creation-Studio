"use client";
import { UserProfile } from "@clerk/nextjs";
export default function SettingsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">Account, organization, billing.</p>
      </div>
      <UserProfile routing="hash" />
    </div>
  );
}
