import { useRef, useState } from "react";
import { api } from "../api";
import { Badge, Button, Card, COLORS, Stat } from "../ui";

interface Labels { direction: string; vs_expectation: string; certainty: string; event_type?: string }
interface Evidence { start: number; end: number; quote: string; rule: string; negated: boolean; polarity: number }
interface Clause { start: number; end: number; text: string; labels: Labels; score: number; evidence: Evidence[]; comparison: null | { metric: string; basis: string; actual: number; expected: number; unit: string; relation: string } }
export interface AnalysisResult {
  model_version: string; language: string; normalized_text: string; labels: Labels;
  quality_status: string; flags: string[]; clauses: Clause[]; evidence: Evidence[];
  reaction: { directional_strength: number };
}

const WORDS: Record<string, string> = {
  bullish: "偏利多", bearish: "偏利空", neutral: "中性", unknown: "未知",
  above: "超出预期", below: "低于预期", inline: "符合预期", no_expectation: "未提供明确预期",
  certain: "确定事实", conditional: "有条件", speculative: "推测或计划",
  zh: "中文", en: "英文", mixed: "中英混合", rule_supported: "有规则证据", needs_review: "需要复核",
  mixed_direction: "不同分句方向不一致", conflicting_expectations: "多个预期判断冲突", mixed_certainty: "事实与计划等语气混合",
  target_not_found: "未找到目标公司", multiple_targets_in_clause: "同一分句包含多个公司", insufficient_financial_evidence: "缺少金融情绪证据",
  ambiguous_negation: "复杂否定需人工判断", negated_expectation: "预期表述被否定", numeric_units_not_comparable: "数值单位不可直接比较",
  numeric_multiple_metrics: "存在多个财务指标", numeric_periods_not_comparable: "数值所属期间不同",
  numeric_reference_order_unsupported: "预期与实际顺序需人工核查", clause_limit_exceeded: "分句过多需缩短输入",
  numeric_negated_reference: "预期数值被否定或语义不明确", out_of_financial_domain: "未识别到金融语境",
  numeric_out_of_range: "数值超出支持范围",
  numeric_bases_not_comparable: "数值水平与变化幅度不可直接比较", unattributed_sentence: "后续分句缺少明确公司归属，未自动关联",
  profit: "利润", revenue: "收入", eps: "每股收益", margin: "利润率", cost: "成本或费用",
  growth: "相对变化率", change: "变化量", level: "数值水平",
  percentage_point: "个百分点", percent: "%", cny: "人民币元", usd: "美元",
};
const label = (value: string) => WORDS[value] ?? value;
const EXAMPLES = [
  { label: "中文否定", text: "公司公告未获批新药上市许可。", target: "", others: "" },
  { label: "英文预期", text: "Acme reported EPS of $1.20 versus expected $1.50.", target: "Acme", others: "" },
  { label: "中英混合", text: "甲公司 reported revenue growth of 30%, versus expectations of 50%.", target: "甲公司", others: "" },
  { label: "多公司", text: "Beta reported revenue growth. Acme reported losses.", target: "Acme", others: "Beta" },
  { label: "中文单位", text: "公司毛利率提高50个基点，预期提高0.4个百分点。", target: "", others: "" },
  { label: "英文否定", text: "The company drug hasn't been approved.", target: "", others: "" },
];

export function AnalysisOutput({ result }: { result: AnalysisResult }) {
  return <>
    <Card title="情绪分析结果" extra={<Badge text={label(result.quality_status)} tone={result.quality_status === "rule_supported" ? "ok" : "warn"} />}>
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
        <Stat label="输入语言" value={label(result.language)} />
        <Stat label="情绪倾向" value={label(result.labels.direction)} />
        <Stat label="相对预期" value={label(result.labels.vs_expectation)} />
        <Stat label="文本语气" value={label(result.labels.certainty)} />
      </div>
      <p style={{ color: COLORS.muted, fontSize: 13, lineHeight: 1.8 }}>
        规则反应强度：{result.reaction.directional_strength.toFixed(3)}。这是对当前文字的规则评分，尚未校准为概率，也不预测股价涨跌幅。
      </p>
      {result.flags.length > 0 && <p role="status" style={{ color: COLORS.warn, lineHeight: 1.8 }}>{result.flags.map(label).join("；")}</p>}
    </Card>
    <Card title="分句与证据">
      {result.clauses.map((clause, index) => <article key={`${clause.start}-${clause.end}`} style={{ padding: "12px 0", borderBottom: `1px solid ${COLORS.border}`, fontSize: 13, lineHeight: 1.8, minWidth: 0, overflowWrap: "anywhere" }}>
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, color: COLORS.muted }}>
          <span>分句 {index + 1}</span>
          <Badge text={label(clause.labels.direction)} tone={clause.labels.direction === "unknown" ? "warn" : clause.labels.direction === "bullish" ? "ok" : clause.labels.direction === "bearish" ? "danger" : "muted"} />
          <span>{label(clause.labels.certainty)} · {label(clause.labels.vs_expectation)}</span>
        </div>
        <p style={{ whiteSpace: "pre-wrap", margin: "8px 0" }}>{clause.text}</p>
        {clause.comparison && <p style={{ margin: "6px 0" }}>数值比较：{label(clause.comparison.metric)}（{label(clause.comparison.basis)}）实际 {clause.comparison.actual} / 预期 {clause.comparison.expected} {label(clause.comparison.unit)}</p>}
        <p style={{ color: COLORS.muted, margin: "6px 0" }}>命中证据：{clause.evidence.map((e) => `${e.quote}${e.negated ? "（被否定）" : ""}`).join("；") || "证据不足"}</p>
      </article>)}
      {result.clauses.length === 0 && <p style={{ color: COLORS.muted, fontSize: 13 }}>没有找到可归属到目标的分句，请核对公司名称或原文。</p>}
      <details style={{ marginTop: 14, fontSize: 13 }}>
        <summary>查看规范化文本与完整结果</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 440, overflow: "auto" }}>{JSON.stringify(result, null, 2)}</pre>
      </details>
    </Card>
  </>;
}

export function AnalysisPage() {
  const [text, setText] = useState(EXAMPLES[2]?.text ?? "");
  const [target, setTarget] = useState(EXAMPLES[2]?.target ?? "");
  const [aliases, setAliases] = useState("");
  const [others, setOthers] = useState("");
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const requestId = useRef(0);
  const run = async () => {
    const id = ++requestId.current;
    setBusy(true); setError(""); setResult(null);
    try {
      const split = (s: string) => s.split(/[,，;；\n]/).map((x) => x.trim()).filter(Boolean);
      const value = await api.post<AnalysisResult>("/api/analysis/text", { text, target: target.trim(), aliases: split(aliases), other_targets: split(others) });
      if (id === requestId.current) setResult(value);
    } catch (e) { if (id === requestId.current) setError((e as Error).message); } finally { if (id === requestId.current) setBusy(false); }
  };
  const resetResult = () => { requestId.current++; setBusy(false); setResult(null); setError(""); };
  return <div>
    <Card title="双语情绪分析">
      <p style={{ marginTop: 0, lineHeight: 1.8, fontSize: 13 }}>粘贴中文、英文或中英混合财经文本，检查公司目标、否定语气、预期落差和支持证据。</p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
        {EXAMPLES.map((example) => <Button key={example.label} kind="default" onClick={() => { setText(example.text); setTarget(example.target); setOthers(example.others); setAliases(""); resetResult(); }}>{example.label}样例</Button>)}
      </div>
      <label style={fieldStyle}>财经文本
        <textarea aria-label="财经文本" maxLength={18000} rows={7} value={text} onChange={(e) => { setText(e.target.value); resetResult(); }} style={{ ...inputStyle, width: "100%", boxSizing: "border-box", lineHeight: 1.7, resize: "vertical" }} />
      </label>
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 12 }}>
        <label style={fieldStyle}>目标公司（可选）<input aria-label="目标公司" maxLength={100} value={target} onChange={(e) => { setTarget(e.target.value); resetResult(); }} style={inputStyle} placeholder="例如 甲公司 或 Acme" /></label>
        <label style={fieldStyle}>目标别名（逗号分隔）<input aria-label="目标别名" value={aliases} onChange={(e) => { setAliases(e.target.value); resetResult(); }} style={inputStyle} placeholder="同一公司的简称、代码" /></label>
        <label style={fieldStyle}>其他公司（逗号分隔）<input aria-label="其他公司" value={others} onChange={(e) => { setOthers(e.target.value); resetResult(); }} style={inputStyle} placeholder="例如 Beta，乙公司" /></label>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 16 }}>
        <Button disabled={busy || !text.trim()} onClick={run}>{busy ? "分析中…" : "分析文本"}</Button>
        <span style={{ fontSize: 12, color: COLORS.muted }}>{text.length} / 18000 字符</span>
      </div>
      {error && <p role="alert" style={{ color: COLORS.danger }}>{error}</p>}
    </Card>
    {result && <AnalysisOutput result={result} />}
  </div>;
}

const inputStyle: React.CSSProperties = { padding: "8px 10px", border: `1px solid ${COLORS.border}`, borderRadius: 6, fontSize: 14, minWidth: 190, background: "#fff" };
const fieldStyle: React.CSSProperties = { display: "flex", flexDirection: "column", gap: 7, fontSize: 13 };
