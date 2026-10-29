import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { api, getToken, setToken, type LoginResponse } from "./api";
import { Button, Card, COLORS } from "./ui";
import { OverviewPage } from "./pages/Overview";
import { TextsPage } from "./pages/Texts";
import { LabelingPage } from "./pages/Labeling";
import { ExperimentsPage } from "./pages/Experiments";
import { QualityPage } from "./pages/Quality";
import { AuditPage } from "./pages/Audit";
import { AnalysisPage } from "./pages/Analysis";

type Page = "overview" | "texts" | "analysis" | "labeling" | "experiments" | "quality" | "audit";

const NAV: { key: Page; label: string; roles: string[] }[] = [
  { key: "overview", label: "总览", roles: ["admin", "researcher", "adjudicator", "annotator", "viewer"] },
  { key: "texts", label: "文本浏览", roles: ["admin", "researcher", "adjudicator", "annotator", "viewer"] },
  { key: "analysis", label: "双语情绪分析", roles: ["admin", "researcher", "adjudicator", "annotator", "viewer"] },
  { key: "labeling", label: "标注工作台", roles: ["admin", "researcher", "adjudicator", "annotator"] },
  { key: "experiments", label: "实验中心", roles: ["admin", "researcher", "adjudicator", "annotator", "viewer"] },
  { key: "quality", label: "数据质量", roles: ["admin", "researcher", "adjudicator", "annotator", "viewer"] },
  { key: "audit", label: "审计日志", roles: ["admin"] },
];

function LoginScreen({ onLogin }: { onLogin: (role: string, display: string) => void }) {
  const [username, setUsername] = useState("researcher");
  const [password, setPassword] = useState("Demo12345!");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.post<LoginResponse>("/api/auth/login", { username, password });
      setToken(r.access_token);
      onLogin(r.role, r.display_name);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div style={{ display: "flex", minHeight: "100vh", alignItems: "center", justifyContent: "center", background: COLORS.bg }}>
      <div style={{ width: 360 }}>
        <Card title="AshareSignal Lab · 研究工作台登录">
          <div style={{ display: "grid", gap: 10 }}>
            <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="用户名" style={inputStyle} />
            <input
              value={password}
              type="password"
              onChange={(e) => setPassword(e.target.value)}
              placeholder="密码"
              onKeyDown={(e) => e.key === "Enter" && submit()}
              style={inputStyle}
            />
            {err && <div style={{ color: COLORS.danger, fontSize: 13 }}>{err}</div>}
            <Button onClick={submit} disabled={busy}>
              {busy ? "登录中…" : "登录"}
            </Button>
            <div style={{ fontSize: 12, color: COLORS.muted }}>
              演示账号：admin / researcher / adjudicator / annotator_a / annotator_b / viewer，密码 Demo12345!
              <br />
              本系统为研究工具，不提供投资建议，禁止真实交易。
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  padding: "8px 10px",
  border: `1px solid ${COLORS.border}`,
  borderRadius: 6,
  fontSize: 14,
};

function App() {
  const [authed, setAuthed] = useState<boolean>(!!getToken());
  const [role, setRole] = useState<string>("");
  const [display, setDisplay] = useState<string>("");
  const [page, setPage] = useState<Page>("overview");

  if (!authed) {
    return (
      <LoginScreen
        onLogin={(r, d) => {
          setRole(r);
          setDisplay(d);
          setAuthed(true);
        }}
      />
    );
  }

  const nav = NAV.filter((n) => n.roles.includes(role));

  return (
    <div style={{ minHeight: "100vh", background: COLORS.bg }}>
      <header
        style={{
          background: "#101828",
          color: "#fff",
          padding: "8px 20px",
          display: "flex",
          alignItems: "center",
          flexWrap: "wrap",
          rowGap: 6,
          gap: 14,
          minHeight: 52,
        }}
      >
        <strong style={{ fontSize: 15, whiteSpace: "nowrap" }}>AshareSignal Lab</strong>
        <span style={{ fontSize: 12, color: "#98a2b3", whiteSpace: "nowrap" }}>细粒度情绪 × 市场反应研究系统</span>
        <nav style={{ display: "flex", gap: 4, marginLeft: 24, flexWrap: "wrap" }}>
          {nav.map((n) => (
            <button
              key={n.key}
              onClick={() => setPage(n.key)}
              style={{
                background: page === n.key ? "#1f2c47" : "transparent",
                color: page === n.key ? "#fff" : "#98a2b3",
                border: "none",
                padding: "8px 12px",
                borderRadius: 6,
                cursor: "pointer",
                fontSize: 13,
                whiteSpace: "nowrap",
              }}
            >
              {n.label}
            </button>
          ))}
        </nav>
        <div style={{ marginLeft: "auto", fontSize: 13, color: "#cbd5e1", display: "flex", gap: 12, alignItems: "center", whiteSpace: "nowrap" }}>
          <span>
            {display}（{role}）
          </span>
          <button
            onClick={() => {
              setToken(null);
              setAuthed(false);
            }}
            style={{ background: "transparent", color: "#98a2b3", border: "none", cursor: "pointer", fontSize: 13 }}
          >
            退出
          </button>
        </div>
      </header>
      <main style={{ padding: 20, maxWidth: 1280, margin: "0 auto" }}>
        {page === "overview" && <OverviewPage role={role} onGo={setPage} />}
        {page === "texts" && <TextsPage />}
        {page === "analysis" && <AnalysisPage />}
        {page === "labeling" && <LabelingPage />}
        {page === "experiments" && <ExperimentsPage />}
        {page === "quality" && <QualityPage />}
        {page === "audit" && <AuditPage />}
      </main>
      <footer style={{ textAlign: "center", color: COLORS.muted, fontSize: 12, padding: 16 }}>
        研究用途 · 结果不构成投资建议 · 真实交易已被系统策略禁用
      </footer>
    </div>
  );
}

const root = createRoot(document.getElementById("root")!);
root.render(<App />);
