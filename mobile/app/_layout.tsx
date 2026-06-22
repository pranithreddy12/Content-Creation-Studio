import { ClerkProvider, ClerkLoaded } from "@clerk/clerk-expo";
import { Stack } from "expo-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { StatusBar } from "expo-status-bar";
import { tokenCache } from "@/lib/auth";
import { registerForPush } from "@/lib/notifications";
import { useApi } from "@/lib/api";
import { useAuth } from "@clerk/clerk-expo";

const publishableKey =
  process.env.EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY!;

export default function RootLayout() {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, retry: 1 } } })
  );

  return (
    <ClerkProvider publishableKey={publishableKey} tokenCache={tokenCache}>
      <QueryClientProvider client={client}>
        <ClerkLoaded>
          <PushBridge />
          <Stack screenOptions={{ headerShown: false, animation: "fade" }}>
            <Stack.Screen name="(tabs)" />
            <Stack.Screen name="sign-in" />
          </Stack>
          <StatusBar style="light" />
        </ClerkLoaded>
      </QueryClientProvider>
    </ClerkProvider>
  );
}

function PushBridge() {
  const { isSignedIn } = useAuth();
  const api = useApi();
  useEffect(() => {
    if (!isSignedIn) return;
    (async () => {
      const token = await registerForPush();
      if (token) {
        try { await api.post("/notifications/register", { token, platform: "expo" }); } catch {}
      }
    })();
  }, [isSignedIn, api]);
  return null;
}
