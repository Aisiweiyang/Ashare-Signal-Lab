/** 审计日志页（仅 admin）。 */

import { api, type AuditRow } from "../api";
import { Badge, Card, Table, useAsync } from "../ui";

export function AuditPage() {
  const { data, error, loading } = useAsync<{ items: AuditRow[] }>(() => api.get("/api/admin/audit?limit=300"), []);
  return (
    <Card title="审计日志（actor / action / target / 时间 / 结果，只追加）">
      {error && <div style={{ color: "#d64541", fontSize: 13 }}>{error}</div>}
      {loading ? (
        <div style={{ color: "#6a737d" }}>加载中…</div>
      ) : (
        <Table
          head={["时间", "操作者", "动作", "目标", "结果"]}
          rows={(data?.items ?? []).map((r) => [
            r.created_at.replace("T", " ").slice(0, 19),
            r.actor,
            r.action,
            r.target || "—",
            r.result === "ok" ? <Badge text="成功" tone="ok" /> : <Badge text={r.result} tone="warn" />,
          ])}
        />
      )}
    </Card>
  );
}
