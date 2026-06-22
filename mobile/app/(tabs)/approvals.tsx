import { FlatList, View, Text, Pressable, StyleSheet, Alert } from "react-native";
import { useApi, useApiQuery } from "@/lib/api";
import { theme } from "@/lib/theme";

interface Asset { id: string; format: string; title?: string; body?: string; status: string }

export default function Approvals() {
  const api = useApi();
  const { data: assets = [], refetch } = useApiQuery<Asset[]>(["approvals"], "/assets?status=review");

  async function act(id: string, action: "approve" | "reject") {
    try { await api.post(`/assets/${id}/${action}`); refetch(); }
    catch (e) { Alert.alert("Error", (e as Error).message); }
  }

  return (
    <FlatList
      style={{ flex: 1, backgroundColor: theme.bg }}
      contentContainerStyle={{ padding: 16, gap: 12 }}
      data={assets}
      keyExtractor={(it) => it.id}
      ListEmptyComponent={<Text style={{ color: theme.textMuted }}>Inbox zero.</Text>}
      renderItem={({ item }) => (
        <View style={styles.card}>
          <View style={{ flexDirection: "row", justifyContent: "space-between", marginBottom: 8 }}>
            <Text style={styles.badge}>{item.format}</Text>
            <Text style={styles.badge}>{item.status}</Text>
          </View>
          <Text style={styles.title}>{item.title || "Untitled"}</Text>
          <Text style={styles.body} numberOfLines={5}>{item.body}</Text>
          <View style={{ flexDirection: "row", gap: 8, marginTop: 12 }}>
            <Pressable style={[styles.btn, { backgroundColor: theme.success }]} onPress={() => act(item.id, "approve")}>
              <Text style={styles.btnText}>Approve</Text>
            </Pressable>
            <Pressable style={[styles.btn, { backgroundColor: theme.danger }]} onPress={() => act(item.id, "reject")}>
              <Text style={styles.btnText}>Reject</Text>
            </Pressable>
          </View>
        </View>
      )}
    />
  );
}

const styles = StyleSheet.create({
  card: { padding: 14, backgroundColor: theme.card, borderRadius: 12, borderWidth: 1, borderColor: theme.border },
  title: { color: theme.text, fontWeight: "600", marginBottom: 6 },
  body: { color: theme.textMuted, fontSize: 13, lineHeight: 18 },
  badge: { color: theme.text, fontSize: 11, paddingHorizontal: 8, paddingVertical: 3,
           backgroundColor: theme.bg, borderRadius: 999, borderWidth: 1, borderColor: theme.border },
  btn: { flex: 1, paddingVertical: 10, borderRadius: 8, alignItems: "center" },
  btnText: { color: "#000", fontWeight: "600" },
});
