import { useRef, useState } from "react";
import { KeyboardAvoidingView, Platform, View, Text, TextInput, Pressable, FlatList, StyleSheet } from "react-native";
import { useApi } from "@/lib/api";
import { theme } from "@/lib/theme";

interface Msg { id: string; role: "user" | "assistant"; text: string }

export default function Chat() {
  const api = useApi();
  const [msgs, setMsgs] = useState<Msg[]>([
    { id: "0", role: "assistant", text: "Ask me anything about your content. I can re-write, plan, or pull last week's results." },
  ]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const list = useRef<FlatList<Msg>>(null);

  async function send() {
    if (!input.trim()) return;
    const id = String(Date.now());
    const userMsg: Msg = { id, role: "user", text: input.trim() };
    setMsgs((m) => [...m, userMsg]);
    setInput("");
    setSending(true);
    try {
      const r = await api.post<{ reply: string }>("/agents/chat", {
        history: [...msgs, userMsg].map((m) => ({ role: m.role, content: m.text })),
      });
      setMsgs((m) => [...m, { id: id + "a", role: "assistant", text: r.reply }]);
    } catch (e) {
      setMsgs((m) => [...m, { id: id + "e", role: "assistant", text: "Sorry — that failed: " + (e as Error).message }]);
    } finally { setSending(false); }
  }

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: theme.bg }}
      behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <FlatList
        ref={list}
        data={msgs}
        keyExtractor={(m) => m.id}
        contentContainerStyle={{ padding: 12, gap: 8 }}
        onContentSizeChange={() => list.current?.scrollToEnd({ animated: true })}
        renderItem={({ item }) => (
          <View style={[styles.bubble, item.role === "user" ? styles.user : styles.assistant]}>
            <Text style={item.role === "user" ? styles.userText : styles.assistantText}>{item.text}</Text>
          </View>
        )}
      />
      <View style={styles.composer}>
        <TextInput style={styles.input} value={input} onChangeText={setInput}
          placeholder="Message Studio assistant…" placeholderTextColor={theme.textMuted} multiline />
        <Pressable style={styles.send} onPress={send} disabled={sending}>
          <Text style={{ color: theme.primaryFg, fontWeight: "600" }}>{sending ? "…" : "Send"}</Text>
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  bubble: { padding: 12, borderRadius: 14, maxWidth: "85%" },
  user: { backgroundColor: theme.text, alignSelf: "flex-end" },
  assistant: { backgroundColor: theme.card, alignSelf: "flex-start", borderWidth: 1, borderColor: theme.border },
  userText: { color: theme.primaryFg },
  assistantText: { color: theme.text },
  composer: { flexDirection: "row", alignItems: "flex-end", gap: 8, padding: 10, borderTopColor: theme.border, borderTopWidth: 1, backgroundColor: theme.bg },
  input: { flex: 1, color: theme.text, backgroundColor: theme.card, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 10, borderWidth: 1, borderColor: theme.border, maxHeight: 120 },
  send: { backgroundColor: theme.primary, paddingHorizontal: 16, paddingVertical: 12, borderRadius: 10 },
});
