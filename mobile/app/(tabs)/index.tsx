import { ScrollView, View, Text, StyleSheet } from "react-native";
import { useApiQuery } from "@/lib/api";
import { theme } from "@/lib/theme";

interface Overview { generated: number; scheduled: number; published: number; avg_viral_score: number }

export default function Today() {
  const { data } = useApiQuery<Overview>(["overview"], "/analytics/overview");
  return (
    <ScrollView style={{ flex: 1, backgroundColor: theme.bg }} contentContainerStyle={{ padding: 16 }}>
      <Text style={styles.h1}>Today</Text>
      <View style={styles.grid}>
        <Stat label="Generated" value={data?.generated ?? 0} />
        <Stat label="Scheduled" value={data?.scheduled ?? 0} />
        <Stat label="Published" value={data?.published ?? 0} />
        <Stat label="Viral score" value={data?.avg_viral_score?.toFixed?.(2) ?? "—"} />
      </View>
    </ScrollView>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <View style={styles.card}>
      <Text style={styles.cardLabel}>{label}</Text>
      <Text style={styles.cardValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  h1: { color: theme.text, fontSize: 28, fontWeight: "700", marginBottom: 16 },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
  card: {
    width: "48%", padding: 16, backgroundColor: theme.card,
    borderRadius: 12, borderWidth: 1, borderColor: theme.border,
  },
  cardLabel: { color: theme.textMuted, fontSize: 12 },
  cardValue: { color: theme.text, fontSize: 24, fontWeight: "700", marginTop: 6 },
});
