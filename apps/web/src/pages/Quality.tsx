/** 数据质量页：契约断言结果（通过率环图 + 断言明细）。 */

import type * as echarts from "echarts";
import { api, type QualityResult } from "../api";
import { Badge, Button, Card, Chart, Table, useAsync } from "../ui";
import { COLORS } from "../ui";

interface BindingStatus {
  bound: boolean;
  seed?: number | null;
  generator_version?: string;
  code_commit?: string;
  created_at?: string;
}

interface QualityAssertion {
  name: string;
  passed: boolean;
  n_violations: number;
  violations: string[];
  detail: string;
}

export function QualityPage() {
  const { data, error, loading, reload } = useAsync<QualityResult>(() => api.get("/api/data/quality"), []);
  const binding = useAsync<BindingStatus>(() => api.get("/api/data/binding"), []);
  return (
    <Card
      title="数据质量契约"
      extra={
        <Button kind="default" onClick={reload}>
          重新检查
        </Button>
      }
    >
      {error && <div style={{ color: COLORS.danger, fontSize: 13 }}>{error}</div>}
      {loading && <div style={{ color: COLORS.muted }}>检查中…</div>}
      {binding.data && (
        <p style={{ fontSize: 13, margin: "0 0 12px" }}>
          数据版本绑定（ADR-014）：
          {binding.data.bound ? (
            <>
              <Badge text={`seed=${binding.data.seed}`} tone="ok" />
              <span style={{ color: COLORS.muted, marginLeft: 8 }}>
                生成器 {binding.data.generator_version} · 代码 {String(binding.data.code_commit).slice(0, 8)} · 绑定于 {String(binding.data.created_at).slice(0, 19).replace("T", " ")}
              </span>
            </>
          ) : (
            <Badge text="未绑定（跨 seed 实验将被拒绝）" tone="warn" />
          )}
        </p>
      )}
      {data && (
        <>
          <div style={{ display: "flex", gap: 24, alignItems: "center", flexWrap: "wrap" }}>
            <Chart height={170} option={passDonutOption(data)} />
            <div style={{ flex: 1, minWidth: 260 }}>
              <p style={{ fontSize: 13, margin: "0 0 8px" }}>
                共 <b>{data.n_assertions}</b> 项断言：
                {data.passed ? <Badge text="全部通过" tone="ok" /> : <Badge text="存在失败" tone="danger" />}
              </p>
              <p style={{ fontSize: 13, color: COLORS.muted, margin: 0 }}>
                库内文本 <b>{data.n_texts}</b> 条 / 行情 <b>{data.n_bars}</b> 根。
                断言覆盖时间对齐、簇完整性、严格可用标记与行情连续性——任何失败都会阻断下游实验的可信度。
              </p>
            </div>
          </div>
          <Table
            head={["断言", "结果", "违规数", "说明"]}
            rows={(data.assertions as QualityAssertion[]).map((a) => [
              a.name,
              a.passed ? <Badge text="通过" tone="ok" /> : <Badge text="失败" tone="danger" />,
              a.n_violations,
              <span style={{ fontSize: 12, color: COLORS.muted }}>{a.detail || a.violations.slice(0, 3).join("；")}</span>,
            ])}
          />
        </>
      )}
    </Card>
  );
}

function passDonutOption(data: QualityResult): echarts.EChartsOption {
  const passed = data.assertions.filter((a) => a.passed).length;
  const failed = data.n_assertions - passed;
  return {
    title: {
      text: `${data.n_assertions ? Math.round((passed / data.n_assertions) * 100) : 0}%`,
      subtext: "通过率",
      left: "center",
      top: "38%",
      textStyle: { fontSize: 22, color: failed === 0 ? COLORS.ok : COLORS.danger },
      subtextStyle: { fontSize: 12, color: COLORS.muted },
    },
    tooltip: { trigger: "item" as const },
    series: [
      {
        type: "pie",
        radius: ["62%", "84%"],
        center: ["50%", "50%"],
        label: { show: false },
        data: [
          { name: "通过", value: passed, itemStyle: { color: COLORS.ok } },
          { name: "失败", value: failed, itemStyle: { color: COLORS.danger } },
        ].filter((d) => d.value > 0),
      },
    ],
  };
}
