/** 共享 UI 组件与样式常量（轻量自绘，不引组件库）。 */

import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
// 阶段 11：echarts 按需注册（bar/line/pie/custom + grid/tooltip/legend/markLine），
// 未经使用的图表族不进构建产物；类型仍用完整包定义保证 TS strict 不变
import * as echarts from "echarts/core";
import type { EChartsOption } from "echarts";
import { BarChart, CustomChart, LineChart, PieChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  BarChart,
  CustomChart,
  LineChart,
  PieChart,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export const COLORS = {
  bg: "#f5f7fa",
  card: "#ffffff",
  border: "#e4e9f0",
  primary: "#1f5eff",
  danger: "#d64541",
  ok: "#1a9e6b",
  warn: "#e6a23c",
  text: "#24292f",
  muted: "#6a737d",
};

export function Card({ title, children, extra }: { title: string; children: ReactNode; extra?: ReactNode }) {
  return (
    <div
      style={{
        background: COLORS.card,
        border: `1px solid ${COLORS.border}`,
        borderRadius: 8,
        padding: 16,
        marginBottom: 16,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <h3 style={{ margin: 0, fontSize: 15, color: COLORS.text }}>{title}</h3>
        {extra}
      </div>
      {children}
    </div>
  );
}

export function Button(props: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  kind?: "primary" | "danger" | "default";
}) {
  const kind = props.kind ?? "primary";
  const bg = kind === "primary" ? COLORS.primary : kind === "danger" ? COLORS.danger : "#fff";
  return (
    <button
      onClick={props.onClick}
      disabled={props.disabled}
      style={{
        background: bg,
        color: kind === "default" ? COLORS.text : "#fff",
        border: `1px solid ${kind === "default" ? COLORS.border : bg}`,
        borderRadius: 6,
        padding: "6px 14px",
        cursor: props.disabled ? "not-allowed" : "pointer",
        opacity: props.disabled ? 0.6 : 1,
        fontSize: 13,
      }}
    >
      {props.children}
    </button>
  );
}

export function Stat({ label, value, tone }: { label: string; value: string | number; tone?: "ok" | "danger" | "warn" }) {
  const color = tone === "ok" ? COLORS.ok : tone === "danger" ? COLORS.danger : tone === "warn" ? COLORS.warn : COLORS.text;
  return (
    <div style={{ flex: 1, minWidth: 120 }}>
      <div style={{ fontSize: 12, color: COLORS.muted }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 600, color, marginTop: 2 }}>{value}</div>
    </div>
  );
}

export function Table({ head, rows }: { head: string[]; rows: (ReactNode)[][] }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr>
            {head.map((h) => (
              <th
                key={h}
                style={{ textAlign: "left", padding: "8px 10px", borderBottom: `2px solid ${COLORS.border}`, color: COLORS.muted, fontWeight: 500, whiteSpace: "nowrap" }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} style={{ borderBottom: `1px solid ${COLORS.border}` }}>
              {r.map((c, j) => (
                <td key={j} style={{ padding: "8px 10px", color: COLORS.text }}>
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && <div style={{ padding: 16, color: COLORS.muted, textAlign: "center" }}>暂无数据</div>}
    </div>
  );
}

/** ECharts 轻封装。 */
export function Chart({ option, height = 320 }: { option: EChartsOption; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof echarts.init> | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chartRef.current = chart;
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, []);
  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option]);
  return <div ref={ref} style={{ width: "100%", height }} />;
}

/**
 * 置信区间带图：每行一个指标，横条为 95% CI，圆点为点估计，竖线为 0 基准。
 * 一眼可判「区间是否跨 0」（本项目的核心判定语义，见 RESEARCH_PROTOCOL §5）。
 */
export function CiStrip({ rows, unit = "", height }: { rows: CiRowData[]; unit?: string; height?: number }) {
  const dataRows = rows.filter((r) => r.lo !== null && r.hi !== null);
  const h = height ?? 60 + dataRows.length * 44;
  if (dataRows.length === 0) {
    return <div style={{ padding: 12, color: COLORS.muted, fontSize: 13 }}>样本内并列值过多，置信区间不可估（如实显示为空）。</div>;
  }
  // 轴域自适应：以 CI 范围为基准（含 0 基准线），两侧留 15% 边距——否则自定义系列
  // 的数据点全在 x=0，ECharts 会把轴默认成 [0,1] 把区间带压扁
  const loMin = Math.min(...dataRows.map((r) => r.lo as number), 0);
  const hiMax = Math.max(...dataRows.map((r) => r.hi as number), 0);
  const pad = (hiMax - loMin) * 0.15 || 0.05;
  const axisMin = Math.round((loMin - pad) * 1000) / 1000;
  const axisMax = Math.round((hiMax + pad) * 1000) / 1000;
  const option: EChartsOption = {
    grid: { top: 14, right: 40, bottom: 26, left: 110 },
    xAxis: {
      type: "value",
      name: unit,
      min: axisMin,
      max: axisMax,
      nameTextStyle: { color: COLORS.muted, fontSize: 11 },
      axisLabel: { fontSize: 11, color: COLORS.muted },
      splitLine: { lineStyle: { color: COLORS.border } },
    },
    yAxis: {
      type: "category",
      data: dataRows.map((r) => r.label),
      axisLabel: { fontSize: 12, color: COLORS.text },
      axisTick: { show: false },
      axisLine: { show: false },
      inverse: true,
    },
    series: [
      {
        type: "custom",
        renderItem: (params, api) => {
          const row = dataRows[params.dataIndex];
          if (!row) return;
          const y = api.coord([0, params.dataIndex])[1] as number;
          const loX = (api.coord([row.lo as number, params.dataIndex])[0] ?? 0) as number;
          const hiX = (api.coord([row.hi as number, params.dataIndex])[0] ?? 0) as number;
          const pointX = (api.coord([row.delta ?? 0, params.dataIndex])[0] ?? 0) as number;
          const crossesZero = (row.lo as number) < 0 && (row.hi as number) > 0;
          return {
            type: "group",
            children: [
              {
                type: "rect",
                shape: { x: loX, y: y - 9, width: Math.max(hiX - loX, 2), height: 18, r: 3 },
                style: { fill: crossesZero ? COLORS.warn : COLORS.ok, opacity: 0.35 },
              },
              { type: "circle", shape: { cx: pointX, cy: y, r: 5 }, style: { fill: COLORS.primary } },
            ],
          };
        },
        data: dataRows.map((_, i) => [0, i]),
        encode: { x: 0, y: 1 },
      },
      {
        type: "line",
        markLine: {
          silent: true,
          symbol: "none",
          label: { show: false },
          lineStyle: { color: COLORS.danger, type: "dashed", width: 1.2 },
          data: [{ xAxis: 0 }],
        },
        data: [],
      },
    ],
    tooltip: {
      trigger: "item",
      formatter: (p) => {
        const r = dataRows[(p as { dataIndex: number }).dataIndex];
        if (!r) return "";
        const f = (v: number | null) => (v === null ? "—" : v.toFixed(4));
        return `${r.label}<br/>点估计 ${f(r.delta)}<br/>95% CI [${f(r.lo)}, ${f(r.hi)}]${r.note ? `<br/>${r.note}` : ""}`;
      },
    },
  };
  return <Chart option={option} height={h} />;
}

export interface CiRowData {
  label: string;
  delta: number | null;
  lo: number | null;
  hi: number | null;
  note?: string;
}

export function Badge({ text, tone }: { text: string; tone: "ok" | "danger" | "warn" | "muted" }) {
  const map = { ok: COLORS.ok, danger: COLORS.danger, warn: COLORS.warn, muted: COLORS.muted };
  return (
    <span
      style={{
        display: "inline-block",
        padding: "1px 8px",
        borderRadius: 10,
        fontSize: 12,
        color: "#fff",
        background: map[tone],
      }}
    >
      {text}
    </span>
  );
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; error: string | null; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fn()
      .then((d) => alive && setData(d))
      .catch((e: Error) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}

export const DIRECTION_OPTIONS = [
  { value: "bullish", label: "看涨" },
  { value: "bearish", label: "看跌" },
  { value: "neutral", label: "中性" },
  { value: "unknown", label: "无法判断" },
];

export const VS_OPTIONS = [
  { value: "above", label: "超预期" },
  { value: "inline", label: "符合预期" },
  { value: "below", label: "低于预期" },
  { value: "no_expectation", label: "无明确预期" },
  { value: "unknown", label: "无法判断" },
];

export const CERTAINTY_OPTIONS = [
  { value: "certain", label: "确定" },
  { value: "speculative", label: "推测" },
  { value: "conditional", label: "有条件" },
  { value: "unknown", label: "无法判断" },
];

export function labelOf(options: { value: string; label: string }[], value: string | null | undefined): string {
  if (!value) return "—";
  return options.find((o) => o.value === value)?.label ?? value;
}
