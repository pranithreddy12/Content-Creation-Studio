import { ScrollView, View, Text, StyleSheet } from "react-native";
import { useApiQuery } from "@/lib/api";
import { theme } from "@/lib/theme";

interface Row { platform: string; views: number; likes: number; shares: number; ctr: number }

export default function Analytics() {
  const { data } = useApiQuery<Row[]>(["timeseries"], "/analytics/timeseries?window=30d");
  const rows: Row[] = data ?? [];
  return (
    <ScrollView style={{ flex: 1, backgroundColor: theme.bg }} contentContainerStyle={{ padding: 16 }}>
      <Text style={styles.h1}>30-day performance</Text>
      {rows.map((r: Row, i: number) => (
        <View key={i} style={styles.row}>
          <Text style={styles.platform}>{r.platform}</Text>
          <View style={{ flexDirection: "row", gap: 12 }}>
            <Cell label="Views" value={r.views} />
            <Cell label="Likes" value={r.likes} />
            <Cell label="Shares" value={r.shares} />
          </View>
        </View>
      ))}
    </ScrollView>
  );
}

function Cell({ label, value }: { label: string; value: number }) {
  return (
    <View><Text style={styles.cellLabel}>{label}</Text>
      <Text style={styles.cellValue}>{value ?? 0}</Text></View>
  );
}

const styles = StyleSheet.create({
  h1: { color: theme.text, fontSize: 22, fontWeight: "700", marginBottom: 12 },
  row: { padding: 14, backgroundColor: theme.card, borderRadius: 12, borderWidth: 1, borderColor: theme.border, marginBottom: 10 },
  platform: { color: theme.text, fontWeight: "600", marginBottom: 8, textTransform: "capitalize" },
  cellLabel: { color: theme.textMuted, fontSize: 11 },
  cellValue: { color: theme.text, fontWeight: "600" },
});
