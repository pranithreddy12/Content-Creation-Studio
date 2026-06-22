import { useState } from "react";
import { View, Text, TextInput, Pressable, StyleSheet, Alert } from "react-native";
import { useSignIn } from "@clerk/clerk-expo";
import { useRouter } from "expo-router";
import { theme } from "@/lib/theme";

export default function SignInScreen() {
  const { signIn, setActive, isLoaded } = useSignIn();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!isLoaded) return;
    setBusy(true);
    try {
      const r = await signIn.create({ identifier: email, password: pw });
      if (r.status === "complete") {
        await setActive({ session: r.createdSessionId });
        router.replace("/(tabs)");
      } else {
        Alert.alert("Sign in", "Additional steps required.");
      }
    } catch (err) {
      Alert.alert("Sign in failed", (err as Error).message);
    } finally { setBusy(false); }
  }

  return (
    <View style={styles.wrap}>
      <Text style={styles.title}>Studio</Text>
      <Text style={styles.sub}>Sign in to your account</Text>
      <TextInput style={styles.input} placeholder="email" placeholderTextColor={theme.textMuted}
        autoCapitalize="none" keyboardType="email-address" value={email} onChangeText={setEmail} />
      <TextInput style={styles.input} placeholder="password" placeholderTextColor={theme.textMuted}
        secureTextEntry value={pw} onChangeText={setPw} />
      <Pressable style={styles.btn} onPress={submit} disabled={busy}>
        <Text style={styles.btnText}>{busy ? "Signing in…" : "Sign in"}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: theme.bg, padding: 24, justifyContent: "center" },
  title: { color: theme.text, fontSize: 32, fontWeight: "700" },
  sub: { color: theme.textMuted, marginTop: 8, marginBottom: 24 },
  input: {
    backgroundColor: theme.card, color: theme.text, borderRadius: 10,
    paddingHorizontal: 14, paddingVertical: 12, marginBottom: 12,
    borderWidth: 1, borderColor: theme.border,
  },
  btn: { backgroundColor: theme.primary, borderRadius: 10, padding: 14, alignItems: "center", marginTop: 8 },
  btnText: { color: theme.primaryFg, fontWeight: "600" },
});
