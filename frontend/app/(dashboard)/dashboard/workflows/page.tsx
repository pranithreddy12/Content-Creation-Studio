"use client";
import { useCallback, useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  type Connection,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Play, Save, Plus } from "lucide-react";
import { toast } from "sonner";
import { useApi } from "@/lib/api";
import { useStudioStore } from "@/lib/store";

const NODE_TYPES = [
  { type: "trigger.schedule",   label: "Schedule trigger" },
  { type: "trigger.webhook",    label: "Webhook trigger" },
  { type: "trigger.event",      label: "Event trigger" },
  { type: "agent.research",     label: "Research agent" },
  { type: "agent.writer",       label: "Writer agent" },
  { type: "agent.seo",          label: "SEO agent" },
  { type: "agent.video",        label: "Video agent" },
  { type: "agent.publisher",    label: "Publisher" },
  { type: "control.condition",  label: "Condition" },
  { type: "control.loop",       label: "Loop" },
  { type: "control.approval",   label: "Human approval" },
];

const initialNodes: Node[] = [
  { id: "1", position: { x: 80, y: 200 }, data: { label: "Schedule trigger" }, type: "input" },
  { id: "2", position: { x: 360, y: 200 }, data: { label: "Research agent" } },
  { id: "3", position: { x: 640, y: 200 }, data: { label: "Writer agent" } },
  { id: "4", position: { x: 920, y: 200 }, data: { label: "Publisher" }, type: "output" },
];
const initialEdges: Edge[] = [
  { id: "e1", source: "1", target: "2", animated: true },
  { id: "e2", source: "2", target: "3", animated: true },
  { id: "e3", source: "3", target: "4", animated: true },
];

export default function WorkflowsPage() {
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const onConnect = useCallback((c: Connection) => setEdges((eds) => addEdge({ ...c, animated: true }, eds)), [setEdges]);
  const { activeBrandId } = useStudioStore();
  const api = useApi();

  const addNode = useCallback((type: string, label: string) => {
    const id = String(Date.now());
    setNodes((nds) => [...nds, { id, position: { x: 200 + Math.random() * 600, y: 350 + Math.random() * 100 }, data: { label, kind: type } }]);
  }, [setNodes]);

  async function save() {
    if (!activeBrandId) return toast.error("Pick a brand first");
    try {
      await api.post("/workflows", {
        brand_id: activeBrandId,
        name: "Untitled workflow",
        trigger: { kind: "schedule", config: { cron: "0 9 * * *" } },
        definition: { nodes, edges },
      });
      toast.success("Workflow saved");
    } catch (e) { toast.error((e as Error).message); }
  }

  async function run() {
    try {
      toast.info("Running workflow…");
      await api.post("/workflows/run", { definition: { nodes, edges } });
      toast.success("Workflow validated");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  return (
    <div className="grid h-[calc(100vh-7rem)] grid-cols-[260px_1fr] gap-4">
      <Card>
        <CardHeader><CardTitle className="text-base">Nodes</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {NODE_TYPES.map((n) => (
            <Button key={n.type} variant="outline" size="sm" className="w-full justify-start" onClick={() => addNode(n.type, n.label)}>
              <Plus className="mr-2 h-3 w-3" />{n.label}
            </Button>
          ))}
        </CardContent>
      </Card>
      <Card className="relative">
        <div className="absolute right-3 top-3 z-10 flex gap-2">
          <Button size="sm" variant="outline" onClick={save}><Save className="mr-1 h-3 w-3" />Save</Button>
          <Button size="sm" onClick={run}><Play className="mr-1 h-3 w-3" />Run</Button>
        </div>
        <ReactFlow nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect} fitView>
          <Background gap={16} />
          <Controls />
          <MiniMap pannable zoomable />
        </ReactFlow>
      </Card>
    </div>
  );
}
