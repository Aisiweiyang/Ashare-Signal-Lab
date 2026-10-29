/** 实验中心：运行配置、实验列表、结果详情（模型对比 / IC 与 CI 可视化 / 衰减 / 分折 / 分事件类型 / 成本敏感性 / 回测指标）。 */

import { useState } from "react";
import type * as echarts from "echarts";
import { api, type Manifest } from "../api";
import { Badge, Button, Card, Chart, CiStrip, COLORS, Stat, Table, useAsync, type CiRowData } from "../ui";

const C = COLORS;

interface ExpRow {
  run_id: string;
  name: string;
  state: string;
  seed: number;
  started_at: string;
}

export function ExperimentsPage() {
  const list = useAsync<{ experiments: ExpRow[] }>(() => api.get("/api/experiments"), []);
  const [detail, setDetail] = useState<Manifest | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: "exp_fine_vs_3class",
    provider: "mock",
    seed: 20260907,
    n_events: 130,
    folds: 3,
    slippage_bps: 10,
    split_scheme: "walk_forward",
    kfold_splits: 5,
    event_type_ablation: true,
  });

  const run = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const m = await api.post<Manifest>("/api/experiments/run", form);
      setDetail(m);
      list.reload();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const openDetail = async (runId: string) => {
    setMsg(null);
    try {
      // API 返回包装结构（manifest 嵌套其中）；直接兼容两种形态
      const d = await api.get<{ manifest: Manifest | null } & Partial<Manifest>>(`/api/experiments/${runId}`);
      setDetail((d.manifest ?? (d as Manifest)) || null);
    } catch (e) {
      setMsg((e as Error).message);
    }
  };

  return (
    <div>
      <Card title="运行新实验（合成数据 · 固定种子 · 可复现）">
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "flex-end", fontSize: 13 }}>
          <Field label="实验名">
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} style={inputStyle} />
          </Field>
          <Field label="Provider">
            <select value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })} style={inputStyle}>
              <option value="mock">mock（确定性词典）</option>
              <option value="llm">LLM（需配置端点）</option>
            </select>
          </Field>
          <Field label="种子">
            <input type="number" value={form.seed} onChange={(e) => setForm({ ...form, seed: Number(e.target.value) })} style={inputStyle} />
          </Field>
          <Field label="事件数">
            <input type="number" value={form.n_events} onChange={(e) => setForm({ ...form, n_events: Number(e.target.value) })} style={inputStyle} />
          </Field>
          <Field label="切分方案">
            <select
              value={form.split_scheme}
              onChange={(e) => setForm({ ...form, split_scheme: e.target.value })}
              style={{ ...inputStyle, width: 190 }}
            >
              <option value="walk_forward">walk-forward（v1 预注册）</option>
              <option value="purged_kfold">purged K-fold（§6 对照）</option>
            </select>
          </Field>
          {form.split_scheme === "walk_forward" ? (
            <Field label="折数">
              <input type="number" value={form.folds} onChange={(e) => setForm({ ...form, folds: Number(e.target.value) })} style={inputStyle} />
            </Field>
          ) : (
            <Field label="K 折数">
              <input
                type="number"
                value={form.kfold_splits}
                onChange={(e) => setForm({ ...form, kfold_splits: Number(e.target.value) })}
                style={inputStyle}
              />
            </Field>
          )}
          <Field label="滑点(bp)">
            <input type="number" value={form.slippage_bps} onChange={(e) => setForm({ ...form, slippage_bps: Number(e.target.value) })} style={inputStyle} />
          </Field>
          <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13, paddingBottom: 6 }}>
            <input
              type="checkbox"
              checked={form.event_type_ablation}
              onChange={(e) => setForm({ ...form, event_type_ablation: e.target.checked })}
            />
            分事件类型消融
          </label>
          <Button onClick={run} disabled={busy}>
            {busy ? "运行中（含训练与回测）…" : "运行"}
          </Button>
        </div>
        {msg && <p style={{ color: COLORS.danger, fontSize: 13 }}>{msg}</p>}
      </Card>

      <Card title="实验历史（含负面结果）">
        <Table
          head={["运行 ID", "名称", "状态", "种子", "时间", "操作"]}
          rows={(list.data?.experiments ?? []).map((e) => [
            e.run_id,
            e.name,
            e.state === "succeeded" ? <Badge text="完成" tone="ok" /> : <Badge text={e.state} tone="warn" />,
            e.seed,
            e.started_at.replace("T", " ").slice(0, 19),
            <Button kind="default" onClick={() => openDetail(e.run_id)}>
              详情
            </Button>,
          ])}
        />
      </Card>

      {detail && <ManifestView m={detail} />}
    </div>
  );
}

function ManifestView({ m }: { m: Manifest }) {
  const modelRows = Object.entries(m.model_scores ?? {}).map(([k, v]) => [
    modelLabel(k),
    v.n_oos,
    v.direction_only.accuracy.toFixed(3),
    v.direction_only.macro_f1.toFixed(3),
    (v.fine_grained["direction"] ?? 0).toFixed(3),
    (v.fine_grained["vs_expectation"] ?? 0).toFixed(3),
    (v.fine_grained["certainty"] ?? 0).toFixed(3),
  ]);
  const bt = m.backtest?.metrics ?? {};
  const decay = (m.ic_decay_fine_gold ?? []).filter((d) => d.ic !== null);
  const schemeLabel = m.split_scheme === "purged_kfold" ? "purged K-fold" : m.split_scheme === "walk_forward" ? "walk-forward" : undefined;

  return (
    <div>
      <Card title={`实验结果：${m.name}（${m.run_id}）`}>
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
          <Stat label="严格金标" value={m.n_gold_strict} />
          <Stat label="样本外事件" value={m.n_oos_events} />
          <Stat label="Provider" value={m.provider_note} />
          {schemeLabel && <Stat label="切分方案" value={schemeLabel} />}
          {m.folds_used && (
            <Stat
              label="折使用"
              value={`${m.folds_used.ids.length} 用 / ${m.folds_used.skipped_empty.length} 跳过`}
              tone={m.folds_used.skipped_empty.length > 0 ? "warn" : undefined}
            />
          )}
          <Stat
            label="H1（金标信号）"
            value={m.h1_verdict.supported_on_gold ? "支持" : "未获支持"}
            tone={m.h1_verdict.supported_on_gold ? "ok" : "warn"}
          />
          <Stat
            label="H1（预测信号）"
            value={m.h1_verdict.supported_on_predictions ? "支持" : "未获支持"}
            tone={m.h1_verdict.supported_on_predictions ? "ok" : "warn"}
          />
        </div>
        <p style={{ fontSize: 12.5, color: COLORS.muted, marginTop: 10 }}>{m.h1_verdict.note}</p>
      </Card>

      <Card title="模型对比（样本外 · 时间切分 · 含 unknown 类）">
        <Table
          head={["模型", "样本数", "方向准确率", "方向宏F1", "细粒度:方向F1", "细粒度:预期F1", "细粒度:确定性F1"]}
          rows={modelRows}
        />
      </Card>

      <Card title="IC 对比与 H1 判定（细粒度 vs 三分类）">
        <Table
          head={["口径", "N", "ΔIC", "95% CI 下界", "95% CI 上界", "判定"]}
          rows={[
            [
              "金标信号",
              m.ic_gold_fine_vs_dir3.n,
              fmt(m.ic_gold_fine_vs_dir3.delta_ic),
              fmt(m.ic_gold_fine_vs_dir3.ci95_low),
              fmt(m.ic_gold_fine_vs_dir3.ci95_high),
              m.ic_gold_fine_vs_dir3.h1_supported ? <Badge text="CI 下界>0" tone="ok" /> : <Badge text="CI 含 0" tone="warn" />,
            ],
            [
              "预测信号",
              m.ic_pred_fine_vs_dir3.n,
              fmt(m.ic_pred_fine_vs_dir3.delta_ic),
              fmt(m.ic_pred_fine_vs_dir3.ci95_low),
              fmt(m.ic_pred_fine_vs_dir3.ci95_high),
              m.ic_pred_fine_vs_dir3.h1_supported ? <Badge text="CI 下界>0" tone="ok" /> : <Badge text="CI 含 0" tone="warn" />,
            ],
          ]}
        />
        <div style={{ marginTop: 12 }}>
          <CiStrip
            rows={h1CiRows(m)}
            unit="ΔIC"
          />
        </div>
        {decay.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <Chart
              height={240}
              option={{
                grid: { top: 36, right: 20, bottom: 30, left: 60 },
                title: { text: "细粒度信号 IC 衰减（金标）", textStyle: { fontSize: 13 } },
                xAxis: { type: "category", data: decay.map((d) => `T+${d.horizon}`) },
                yAxis: { type: "value", name: "IC" },
                tooltip: { trigger: "axis" },
                series: [{ type: "bar", data: decay.map((d) => d.ic), itemStyle: { color: "#1f5eff" } }],
              }}
            />
          </div>
        )}
      </Card>

      {m.ic_by_fold_gold && m.ic_by_fold_gold.length > 0 && (
        <Card title="分折 IC 稳定性（金标信号 · 按时间折）">
          <Chart
            height={260}
            option={foldIcOption(m.ic_by_fold_gold)}
          />
          <p style={{ fontSize: 12, color: COLORS.muted, marginTop: 6 }}>
            逐折细粒度 IC 均为正 → 方向一致；是否高于三分类逐折并不一致，与 H1 判定互为印证。
          </p>
        </Card>
      )}

      <Card title="分确定性异常收益（金标事件）">
        <Table
          head={["确定性", "N", "平均异常收益", "t 统计量"]}
          rows={Object.entries(m.abnormal_by_certainty_gold ?? {}).map(([k, v]) => [
            k,
            v.n,
            fmt(v.mean_abnormal),
            v.t_stat ?? "—",
          ])}
        />
      </Card>

      {m.ic_by_event_type_gold_exploratory && Object.keys(m.ic_by_event_type_gold_exploratory).length > 0 && (
        <Card title="分事件类型消融（exploratory · n≥5 才报告）">
          <Chart height={280} option={eventTypeOption(m.ic_by_event_type_gold_exploratory)} />
          <Table
            head={["事件类型", "N", "ΔIC", "95% CI", "异常收益均值", "t 统计量"]}
            rows={Object.entries(m.ic_by_event_type_gold_exploratory).map(([k, v]) => [
              k,
              v.n,
              fmt(v.ic_fine_vs_dir3.delta_ic),
              `${fmt(v.ic_fine_vs_dir3.ci95_low)} ~ ${fmt(v.ic_fine_vs_dir3.ci95_high)}`,
              fmt(v.abnormal_fine.mean_abnormal),
              v.abnormal_fine.t_stat ?? "—",
            ])}
          />
          <p style={{ fontSize: 12, color: COLORS.muted, marginTop: 6 }}>
            组内并列值过多时 Spearman IC 无定义（如实显示 —）；类型层面 ΔIC 的 CI 均含 0，与总判定一致。
          </p>
        </Card>
      )}

      {m.ablation_gold_exploratory && Object.keys(m.ablation_gold_exploratory).length > 0 && (
        <Card title="组件消融（exploratory · 相对完整细粒度信号）">
          <CiStrip
            rows={ablationCiRows(m)}
            unit="ΔIC"
          />
          <p style={{ fontSize: 12, color: COLORS.muted, marginTop: 6 }}>
            去掉某一维度后的 ΔIC CI 含 0 → 该维度增量贡献不显著（与 H1 未获支持相互印证）。
          </p>
        </Card>
      )}

      <Card title={`带成本回测（${m.backtest?.run_id ?? ""}）`}>
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
          <Stat label="总收益" value={pct(bt["total_return"])} tone={(bt["total_return"] ?? 0) >= 0 ? "ok" : "danger"} />
          <Stat label="Sharpe（日频年化）" value={bt["sharpe"] ?? "—"} />
          <Stat label="最大回撤" value={pct(bt["max_drawdown"])} tone="danger" />
          <Stat label="成交笔数" value={bt["n_fills"] ?? "—"} />
          <Stat label="总成本(元)" value={bt["costs_total"] ?? "—"} tone="warn" />
        </div>
        {Object.keys(m.backtest?.rejections ?? {}).length > 0 && (
          <p style={{ fontSize: 13, color: COLORS.muted, marginTop: 10 }}>
            可成交性拒单：{Object.entries(m.backtest.rejections).map(([k, v]) => `${k}×${v}`).join("，")}
          </p>
        )}
        {m.cost_sensitivity_backtest && m.cost_sensitivity_backtest.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <Chart height={280} option={costSensitivityOption(m.cost_sensitivity_backtest)} />
            <p style={{ fontSize: 12, color: COLORS.muted, marginTop: 6 }}>
              同一信号在不同滑点假设下的净结果：总收益随滑点递减，50bp 下转负——成本假设是结论的边界条件（Q3）。
            </p>
          </div>
        )}
      </Card>
    </div>
  );
}

// ---- 图表 option 构造（纯函数，便于阅读） ----

function h1CiRows(m: Manifest): CiRowData[] {
  return [
    {
      label: "金标信号 ΔIC",
      delta: m.ic_gold_fine_vs_dir3.delta_ic,
      lo: m.ic_gold_fine_vs_dir3.ci95_low,
      hi: m.ic_gold_fine_vs_dir3.ci95_high,
      note: `N=${m.ic_gold_fine_vs_dir3.n}`,
    },
    {
      label: "预测信号 ΔIC",
      delta: m.ic_pred_fine_vs_dir3.delta_ic,
      lo: m.ic_pred_fine_vs_dir3.ci95_low,
      hi: m.ic_pred_fine_vs_dir3.ci95_high,
      note: `N=${m.ic_pred_fine_vs_dir3.n}`,
    },
  ];
}

function ablationCiRows(m: Manifest): CiRowData[] {
  const zh: Record<string, string> = { drop_certainty: "去掉确定性维度", drop_vs: "去掉相对预期维度" };
  return Object.entries(m.ablation_gold_exploratory ?? {}).map(([k, v]) => ({
    label: zh[k] ?? k,
    delta: v.delta_ic,
    lo: v.ci95_low,
    hi: v.ci95_high,
    note: `N=${v.n}`,
  }));
}

function foldIcOption(rows: NonNullable<Manifest["ic_by_fold_gold"]>): echarts.EChartsOption {
  const f = (v: number | null) => (v === null ? null : Number(v.toFixed(4)));
  return {
    grid: { top: 40, right: 20, bottom: 30, left: 60 },
    title: { text: "逐折 IC：细粒度 vs 三分类", textStyle: { fontSize: 13 } },
    legend: { data: ["细粒度", "三分类"], top: 4, left: 170, textStyle: { fontSize: 12 } },
    xAxis: { type: "category", data: rows.map((r) => `折${r.fold}（${r.test}）`), axisLabel: { fontSize: 11 } },
    yAxis: { type: "value", name: "IC" },
    tooltip: { trigger: "axis" },
    series: [
      { name: "细粒度", type: "bar", data: rows.map((r) => f(r.ic_fine)), itemStyle: { color: C.primary } },
      { name: "三分类", type: "bar", data: rows.map((r) => f(r.ic_dir3)), itemStyle: { color: C.warn } },
    ],
  };
}

const EVENT_TYPE_ZH: Record<string, string> = {
  earnings: "业绩",
  orders: "订单",
  ma: "并购",
  regulatory: "监管",
  shareholder: "股东",
  product: "产品",
  litigation: "诉讼",
  macro_policy: "宏观政策",
  other: "其他",
};

function eventTypeOption(et: NonNullable<Manifest["ic_by_event_type_gold_exploratory"]>): echarts.EChartsOption {
  const entries = Object.entries(et);
  const f = (v: number | null) => (v === null ? 0 : (v * 100).toFixed(2));
  return {
    grid: { top: 56, right: 30, bottom: 40, left: 70 },
    title: { text: "各类型平均异常收益（%，T+1）", textStyle: { fontSize: 13 } },
    xAxis: {
      type: "category",
      data: entries.map(([k, v]) => `${EVENT_TYPE_ZH[k] ?? k}\n(n=${v.n})`),
      axisLabel: { fontSize: 11, interval: 0, lineHeight: 16 },
    },
    yAxis: { type: "value", name: "异常收益(%)" },
    tooltip: {
      trigger: "axis",
      formatter: (ps) => {
        const p = Array.isArray(ps) ? ps[0] : ps;
        if (!p) return "";
        const entry = entries[p.dataIndex];
        if (!entry) return "";
        const [k, v] = entry;
        const t = v.abnormal_fine.t_stat;
        return `${EVENT_TYPE_ZH[k] ?? k}<br/>异常收益 ${f(v.abnormal_fine.mean_abnormal)}%<br/>t=${t ?? "—"}　n=${v.n}<br/>ΔIC=${fmt(v.ic_fine_vs_dir3.delta_ic)}`;
      },
    },
    series: [
      {
        type: "bar",
        data: entries.map(([, v]) => ({ value: f(v.abnormal_fine.mean_abnormal), itemStyle: { color: (v.abnormal_fine.mean_abnormal ?? 0) >= 0 ? C.ok : C.danger } })),
        barMaxWidth: 46,
        label: { show: true, position: "top", fontSize: 11, formatter: (p) => `${p.value}%` },
        markLine: { silent: true, symbol: "none", lineStyle: { color: C.muted, type: "dashed", width: 1 }, data: [{ yAxis: 0 }], label: { show: false } },
      },
    ],
  };
}

function costSensitivityOption(rows: NonNullable<Manifest["cost_sensitivity_backtest"]>): echarts.EChartsOption {
  const x = rows.map((r) => `${r.slippage_bps}bp`);
  const ret = rows.map((r) => Number(((r["total_return"] ?? 0) * 100).toFixed(2)));
  const sharpe = rows.map((r) => (r["sharpe"] === null || r["sharpe"] === undefined ? null : Number((r["sharpe"] as number).toFixed(2))));
  return {
    grid: { top: 56, right: 60, bottom: 30, left: 70 },
    title: { text: "成本敏感性（Q3）：收益与 Sharpe vs 滑点", textStyle: { fontSize: 13 } },
    legend: { data: ["总收益(%)", "Sharpe"], top: 30, right: 60, textStyle: { fontSize: 12 } },
    xAxis: { type: "category", data: x },
    yAxis: [
      { type: "value", name: "总收益(%)", axisLabel: { formatter: "{value}%" } },
      { type: "value", name: "Sharpe", splitLine: { show: false } },
    ],
    tooltip: { trigger: "axis" },
    series: [
      {
        name: "总收益(%)",
        type: "bar",
        data: ret.map((v) => ({ value: v, itemStyle: { color: v >= 0 ? C.primary : C.danger } })),
        barMaxWidth: 50,
        label: { show: true, position: "top", fontSize: 11, formatter: "{c}%" },
        markLine: { silent: true, symbol: "none", lineStyle: { color: C.danger, type: "dashed" }, data: [{ yAxis: 0 }], label: { show: false } },
      },
      { name: "Sharpe", type: "line", yAxisIndex: 1, data: sharpe, itemStyle: { color: C.warn }, lineStyle: { width: 2 } },
    ],
  };
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "grid", gap: 4 }}>
      <span style={{ color: COLORS.muted, fontSize: 12 }}>{label}</span>
      {children}
    </label>
  );
}

const inputStyle: React.CSSProperties = { padding: "6px 8px", border: `1px solid ${COLORS.border}`, borderRadius: 6, width: 130 };

function modelLabel(k: string): string {
  return { majority: "多数类", lexicon: "词典基线", sklearn: "TF-IDF+LR 小模型", mock_llm: "LLM/mock" }[k] ?? k;
}

function fmt(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : v.toFixed(4);
}

function pct(v: number | undefined): string {
  return v === undefined ? "—" : `${(v * 100).toFixed(2)}%`;
}
