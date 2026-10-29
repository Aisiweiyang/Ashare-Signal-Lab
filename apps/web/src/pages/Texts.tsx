/** 文本浏览页：分页列表 + 筛选 + 详情。 */

import { useRef, useState } from "react";
import { api, type TextItem } from "../api";
import { Badge, Button, Card, Table, useAsync } from "../ui";
import { AnalysisOutput, type AnalysisResult } from "./Analysis";

export function TextsPage() {
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [analysisError, setAnalysisError] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [detailId, setDetailId] = useState("");
  const selectionId = useRef(0);
  const [page, setPage] = useState(1);
  const [symbol, setSymbol] = useState("");
  const [strictOnly, setStrictOnly] = useState(false);
  const [detail, setDetail] = useState<{ title: string; cleaned_text: string; available_at: string | null } | null>(null);

  const query = `/api/texts?page=${page}&page_size=15${symbol ? `&security_symbol=${encodeURIComponent(symbol)}` : ""}${strictOnly ? "&strict_only=true" : ""}`;
  const { data, error, loading } = useAsync<{ total: number; items: TextItem[] }>(() => api.get(query), [query]);

  return (
    <div>
      <Card
        title="市场文本"
        extra={
          <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
            <input
              placeholder="证券代码筛选，如 601907"
              value={symbol}
              onChange={(e) => {
                setSymbol(e.target.value);
                setPage(1);
              }}
              style={{ padding: "4px 8px", border: "1px solid #e4e9f0", borderRadius: 6, width: 180 }}
            />
            <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <input
                type="checkbox"
                checked={strictOnly}
                onChange={(e) => {
                  setStrictOnly(e.target.checked);
                  setPage(1);
                }}
              />
              仅严格可用
            </label>
          </div>
        }
      >
        {error && <div style={{ color: "#d64541", fontSize: 13, marginBottom: 8 }}>{error}</div>}
        {analysisError && !detail && <p role="alert" style={{ color: "#d64541" }}>{analysisError}</p>}
        {loading ? (
          <div style={{ color: "#6a737d" }}>加载中…</div>
        ) : (
          <Table
            head={["标题", "证券", "发布时间", "可得时间", "严格可用", "操作"]}
            rows={(data?.items ?? []).map((t) => [
              <span style={{ maxWidth: 380, display: "inline-block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={t.snippet}>
                {t.title || t.snippet}
              </span>,
              t.security_symbol ?? "—",
              t.published_at?.replace("T", " ").slice(0, 16) ?? "—",
              t.available_at?.replace("T", " ").slice(0, 16) ?? "—",
              t.strict_ok ? <Badge text="可用" tone="ok" /> : <Badge text="不可用" tone="warn" />,
              <a
                href="#"
                onClick={async (e) => {
                  e.preventDefault();
                  const selected = ++selectionId.current;
                  setDetail(null); setAnalysis(null); setAnalysisError(""); setAnalyzing(false);
                  try {
                    const value = await api.get<{ title: string; cleaned_text: string; available_at: string | null }>(`/api/texts/${t.market_text_id}`);
                    if (selected === selectionId.current) { setDetail(value); setDetailId(t.market_text_id); }
                  } catch (e) { if (selected === selectionId.current) setAnalysisError((e as Error).message); }
                }}
                style={{ color: "#1f5eff", textDecoration: "none" }}
              >
                查看
              </a>,
            ])}
          />
        )}
        <div style={{ display: "flex", gap: 10, marginTop: 12, alignItems: "center", fontSize: 13 }}>
          <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)} style={pagerStyle}>
            上一页
          </button>
          <span style={{ color: "#6a737d" }}>
            第 {page} 页 / 共 {Math.max(1, Math.ceil((data?.total ?? 0) / 15))} 页（{data?.total ?? 0} 条）
          </span>
          <button
            disabled={data ? page >= Math.ceil(data.total / 15) : true}
            onClick={() => setPage((p) => p + 1)}
            style={pagerStyle}
          >
            下一页
          </button>
        </div>
      </Card>

      {detail && (
        <Card title={detail.title || "文本详情"} extra={<Button kind="default" onClick={() => { selectionId.current++; setDetail(null); setAnalysis(null); setAnalyzing(false); }}>关闭</Button>}>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 13.5, lineHeight: 1.8, margin: 0, fontFamily: "inherit" }}>{detail.cleaned_text}</pre>
          <div style={{ marginTop: 12 }}><Button disabled={analyzing} kind="default" onClick={async () => {
            const selected = selectionId.current;
            setAnalyzing(true); setAnalysisError(""); setAnalysis(null);
            try { const value = await api.get<AnalysisResult>(`/api/analysis/market-text/${detailId}`); if (selected === selectionId.current) setAnalysis(value); }
            catch (e) { if (selected === selectionId.current) setAnalysisError((e as Error).message); } finally { if (selected === selectionId.current) setAnalyzing(false); }
          }}>{analyzing ? "分析中…" : "分析此文本情绪"}</Button></div>
          {analysisError && <p role="alert" style={{ color: "#d64541" }}>{analysisError}</p>}
          <p style={{ fontSize: 12, color: "#6a737d", marginTop: 10 }}>
            可得时间：{detail.available_at?.replace("T", " ") ?? "缺失（严格回测不可用）"}
          </p>
        </Card>
      )}
      {detail && analysis && <AnalysisOutput result={analysis} />}
    </div>
  );
}

const pagerStyle: React.CSSProperties = {
  border: "1px solid #e4e9f0",
  background: "#fff",
  borderRadius: 6,
  padding: "4px 10px",
  cursor: "pointer",
};
