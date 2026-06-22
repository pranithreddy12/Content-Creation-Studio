import { View, Text, Pressable, StyleSheet, Alert } from "react-native";
import { useAuth, useUser } from "@clerk/clerk-expo";
import { theme } from "@/lib/theme";

export default function Settings() {
  const { signOut } = useAuth();
  const { user } = useUser();
  return (
    <View style={{ flex: 1, backgroundColor: theme.bg, padding: 16 }}>
      <View style={styles.card}>
        <Text style={styles.label}>Signed in as</Text>
        <Text style={styles.value}>{user?.primaryEmailAddress?.emailAddress ?? "—"}</Text>
      </View>
      <Pressable style={styles.signOut} onPress={() => signOut().catch((e) => Alert.alert("Error", e.message))}>
        <Text style={{ color: theme.danger, fontWeight: "600" }}>Sign out</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { padding: 14, backgroundColor: theme.card, borderRadius: 12, borderWidth: 1, borderColor: theme.border, marginBottom: 16 },
  label: { color: theme.textMuted, fontSize: 12 },
  value: { color: theme.text, marginTop: 4 },
  signOut: { padding: 14, borderRadius: 12, borderWidth: 1, borderColor: theme.danger, alignItems: "center" },
});
