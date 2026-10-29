/** 总览页：系统状态、研究假设说明、快速操作。 */

import { useState } from "react";
import { api } from "../api";
import { Badge, Button, Card, Stat, useAsync } from "../ui";
import { COLORS } from "../ui";

export function OverviewPage({ role, onGo }: { role: string; onGo: (p: "texts" | "labeling" | "experiments" | "quality") => void }) {
  const texts = useAsync<{ total: number }>(() => api.get("/api/texts?page_size=1"), []);
  const exps = useAsync<{ experiments: { run_id: string; name: string; state: string }[] }>(() => api.get("/api/experiments"), []);
  const quality = useAsync<{ passed: boolean; n_assertions: number }>(() => api.get("/api/data/quality"), []);
  const [seeding, setSeeding] = useState(false);
  const [seedMsg, setSeedMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const canImport = role === "admin" || role === "researcher";
  const latest = exps.data?.experiments?.[0];

  const seedDemo = async () => {
    setSeeding(true);
    setSeedMsg(null);
    try {
      await api.post("/api/data/seed-demo?n_events=100");
      setSeedMsg({ ok: true, text: "播种完成：可进入「文本浏览」查看，或在「实验中心」运行实验。" });
      texts.reload();
      quality.reload();
      exps.reload();
    } catch (e) {
      setSeedMsg({ ok: false, text: (e as Error).message });
    } finally {
      setSeeding(false);
    }
  };

  return (
    <div>
      <Card title="系统状态">
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
          <Stat label="库内文本" value={texts.data?.total ?? "—"} />
          <Stat label="实验运行数" value={exps.data?.experiments?.length ?? "—"} />
          <Stat
            label="数据质量断言"
            value={quality.data ? (quality.data.passed ? `${quality.data.n_assertions} 项全部通过` : "存在失败") : "—"}
            tone={quality.data ? (quality.data.passed ? "ok" : "danger") : undefined}
          />
          <Stat label="真实交易" value="已禁用" tone="ok" />
        </div>
      </Card>

      <Card title="研究假设（预注册 H1）">
        <p style={{ margin: "4px 0", fontSize: 13.5, lineHeight: 1.7 }}>
          相对预期（超预期/符合/低于/无）与确定性（确定/推测/有条件）维度构建的<b>细粒度信号</b>，
          在时间外样本上的短期预测力是否<b>显著优于</b>传统「看涨/看跌/中性」三分类信号。
        </p>
        <p style={{ margin: "4px 0", fontSize: 13, color: COLORS.muted, lineHeight: 1.7 }}>
          判定标准：配对 IC 差的 bootstrap 95% 置信区间下界 &gt; 0（RESEARCH_PROTOCOL §5）。
          若未达标，系统将如实报告「细粒度维度无增量」——<b>本系统允许假设被证伪，不以回测盈利为验收条件。</b>
        </p>
      </Card>

      <Card title="快速操作">
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <Button kind="default" onClick={() => onGo("texts")}>
            浏览文本
          </Button>
          <Button kind="default" onClick={() => onGo("labeling")}>
            进入标注工作台
          </Button>
          <Button kind="default" onClick={() => onGo("experiments")}>
            实验中心
          </Button>
          <Button kind="default" onClick={() => onGo("quality")}>
            数据质量
          </Button>
          {canImport && (
            <Button disabled={seeding} onClick={seedDemo}>
              {seeding ? "播种中…" : "播种演示数据（合成，固定种子）"}
            </Button>
          )}
        </div>
        {seedMsg && (
          <p style={{ fontSize: 13, marginTop: 12, color: seedMsg.ok ? COLORS.ok : COLORS.danger, lineHeight: 1.6 }}>
            {seedMsg.text}
          </p>
        )}
        {latest && (
          <p style={{ fontSize: 13, color: COLORS.muted, marginTop: 12 }}>
            最近实验：<b>{latest.name}</b>{" "}
            <Badge text={latest.state === "succeeded" ? "完成" : latest.state} tone={latest.state === "succeeded" ? "ok" : "warn"} />
          </p>
        )}
      </Card>
    </div>
  );
}
