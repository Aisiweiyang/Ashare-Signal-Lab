/** 标注工作台：任务列表 → 逐条标注（LLM 预标注灰色展示）→ 一致性 → 仲裁队列。 */

import { useState } from "react";
import { api, type Labels, type TaskItemRow } from "../api";
import { Badge, Button, Card, CERTAINTY_OPTIONS, DIRECTION_OPTIONS, Table, VS_OPTIONS, labelOf, useAsync } from "../ui";
import { COLORS } from "../ui";

interface TaskRow {
  task_id: string;
  name: string;
  status: string;
  guide_version: string;
  n_items: number;
  created_by: string;
}

export function LabelingPage() {
  const tasks = useAsync<{ items: TaskRow[] }>(() => api.get("/api/labeling/tasks"), []);
  const [activeTask, setActiveTask] = useState<string | null>(null);
  const adjudications = useAsync<{ items: { item_id: string; ann_a: Labels & { by: string }; ann_b: Labels & { by: string } }[] }>(
    async () => (activeTask ? api.get(`/api/labeling/tasks/${activeTask}/disagreements`) : Promise.resolve({ items: [] })),
    [activeTask],
  );

  if (tasks.error) return <Card title="标注工作台">{tasks.error}</Card>;

  return (
    <div>
      <Card title="标注任务（指南 v1.0 · 双人独立标注）">
        {tasks.data?.items.length ? (
          <Table
            head={["任务", "条目数", "指南版本", "状态", "创建人", "操作"]}
            rows={tasks.data.items.map((t) => [
              t.name,
              t.n_items,
              `v${t.guide_version}`,
              t.status === "open" ? <Badge text="进行中" tone="ok" /> : <Badge text="已关闭" tone="muted" />,
              t.created_by,
              <Button kind="default" onClick={() => setActiveTask(t.task_id)}>
                打开
              </Button>,
            ])}
          />
        ) : (
          <p style={{ color: COLORS.muted, fontSize: 13 }}>暂无任务。请先由研究者在数据页播种演示数据，或通过 API 创建任务。</p>
        )}
      </Card>

      {activeTask && (
        <>
          <AnnotatePanel taskId={activeTask} onChanged={() => { tasks.reload(); adjudications.reload(); }} />
          {adjudications.data && adjudications.data.items.length > 0 && (
            <AdjudicatePanel items={adjudications.data.items} onChanged={() => adjudications.reload()} />
          )}
        </>
      )}
    </div>
  );
}

function AnnotatePanel({ taskId, onChanged }: { taskId: string; onChanged: () => void }) {
  const items = useAsync<{ items: TaskItemRow[] }>(() => api.get(`/api/labeling/tasks/${taskId}/items`), [taskId]);
  const [idx, setIdx] = useState(0);
  const [labels, setLabels] = useState<Labels>({ direction: "neutral", vs_expectation: "no_expectation", certainty: "certain" });
  const [msg, setMsg] = useState<string | null>(null);
  const rows = items.data?.items ?? [];
  const current = rows[idx];

  const submit = async () => {
    if (!current) return;
    setMsg(null);
    try {
      await api.post("/api/labeling/annotations", { item_id: current.item_id, ...labels, rationale: "工作台标注" });
      setMsg("已保存");
      items.reload();
      onChanged();
    } catch (e) {
      setMsg((e as Error).message);
    }
  };

  if (!current) return <Card title="逐条标注">该任务暂无可标注条目。</Card>;

  return (
    <Card
      title={`逐条标注（${idx + 1} / ${rows.length}）`}
      extra={
        <div style={{ display: "flex", gap: 8 }}>
          <Button kind="default" disabled={idx <= 0} onClick={() => setIdx((i) => i - 1)}>
            上一条
          </Button>
          <Button kind="default" disabled={idx >= rows.length - 1} onClick={() => setIdx((i) => i + 1)}>
            下一条
          </Button>
        </div>
      }
    >
      <div style={{ fontSize: 13.5, background: "#f8fafc", border: `1px solid ${COLORS.border}`, borderRadius: 6, padding: 12, lineHeight: 1.8 }}>
        {current.market_text_id}
      </div>
      {current.preview_labels && (
        <p style={{ fontSize: 12.5, color: COLORS.muted, margin: "10px 0 0" }}>
          LLM 预标注（灰色仅供参考，<b>不进入金标</b>）：{labelOf(DIRECTION_OPTIONS, current.preview_labels.direction)} ·{" "}
          {labelOf(VS_OPTIONS, current.preview_labels.vs_expectation)} · {labelOf(CERTAINTY_OPTIONS, current.preview_labels.certainty)}
        </p>
      )}
      <div style={{ display: "flex", gap: 16, marginTop: 12, flexWrap: "wrap" }}>
        <SelectGroup label="方向" options={DIRECTION_OPTIONS} value={labels.direction} onChange={(v) => setLabels({ ...labels, direction: v })} />
        <SelectGroup label="相对预期" options={VS_OPTIONS} value={labels.vs_expectation} onChange={(v) => setLabels({ ...labels, vs_expectation: v })} />
        <SelectGroup label="确定性" options={CERTAINTY_OPTIONS} value={labels.certainty} onChange={(v) => setLabels({ ...labels, certainty: v })} />
        <div style={{ alignSelf: "flex-end" }}>
          <Button onClick={submit}>提交标注</Button>
        </div>
      </div>
      {msg && <p style={{ fontSize: 13, color: COLORS.ok, marginTop: 8 }}>{msg}</p>}
    </Card>
  );
}

function AdjudicatePanel({
  items,
  onChanged,
}: {
  items: { item_id: string; ann_a: Labels & { by: string }; ann_b: Labels & { by: string } }[];
  onChanged: () => void;
}) {
  const [msg, setMsg] = useState<string | null>(null);
  const decide = async (item_id: string, labels: Labels) => {
    setMsg(null);
    try {
      await api.post("/api/labeling/adjudications", { item_id, ...labels, rationale: "工作台仲裁" });
      setMsg("仲裁完成");
      onChanged();
    } catch (e) {
      setMsg((e as Error).message);
    }
  };
  return (
    <Card title="分歧仲裁队列（仲裁员）">
      {msg && <p style={{ fontSize: 13, color: COLORS.ok }}>{msg}</p>}
      <Table
        head={["条目", "标注 A", "标注 B", "仲裁操作"]}
        rows={items.map((it) => [
          it.item_id,
          `${it.ann_a.by}：${labelOf(DIRECTION_OPTIONS, it.ann_a.direction)} / ${labelOf(VS_OPTIONS, it.ann_a.vs_expectation)} / ${labelOf(CERTAINTY_OPTIONS, it.ann_a.certainty)}`,
          `${it.ann_b.by}：${labelOf(DIRECTION_OPTIONS, it.ann_b.direction)} / ${labelOf(VS_OPTIONS, it.ann_b.vs_expectation)} / ${labelOf(CERTAINTY_OPTIONS, it.ann_b.certainty)}`,
          <span style={{ display: "flex", gap: 6 }}>
            <Button kind="default" onClick={() => decide(it.item_id, { direction: it.ann_a.direction, vs_expectation: it.ann_a.vs_expectation, certainty: it.ann_a.certainty })}>
              采纳 A
            </Button>
            <Button kind="default" onClick={() => decide(it.item_id, { direction: it.ann_b.direction, vs_expectation: it.ann_b.vs_expectation, certainty: it.ann_b.certainty })}>
              采纳 B
            </Button>
          </span>,
        ])}
      />
    </Card>
  );
}

function SelectGroup({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label style={{ fontSize: 13, display: "grid", gap: 4 }}>
      <span style={{ color: COLORS.muted }}>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} style={{ padding: "6px 8px", border: `1px solid ${COLORS.border}`, borderRadius: 6, minWidth: 130 }}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
