/** API 客户端：token 管理 + 统一错误处理。 */

const TOKEN_KEY = "asg_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> | undefined),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (options.body && typeof options.body === "string") {
    headers["Content-Type"] = "application/json";
  }
  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 401) {
    setToken(null);
    throw new ApiError(401, "登录已过期，请重新登录");
  }
  const text = await resp.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!resp.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : `请求失败（${resp.status}）`;
    throw new ApiError(resp.status, detail);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
};

// ---- 后端类型 ----

export interface LoginResponse {
  access_token: string;
  role: string;
  display_name: string;
}

export interface TextItem {
  market_text_id: string;
  title: string;
  snippet: string;
  security_symbol: string | null;
  published_at: string | null;
  available_at: string | null;
  strict_ok: boolean;
  cluster_id: string | null;
  quality_flags: Record<string, unknown>;
}

export interface TaskItemRow {
  item_id: string;
  market_text_id: string;
  order_no: number;
  my_annotation: Labels | null;
  preview_labels: Labels | null;
  quality_state: string;
}

export interface Labels {
  direction: string;
  vs_expectation: string;
  certainty: string;
}

export interface ExperimentSummary {
  run_id: string;
  name: string;
  state: string;
  seed: number;
  started_at: string;
}

export interface IcResult {
  n: number;
  delta_ic: number | null;
  ci95_low: number | null;
  ci95_high: number | null;
  h1_supported?: boolean;
}

export interface ModelScore {
  n_oos: number;
  direction_only: { accuracy: number; macro_f1: number };
  fine_grained: Record<string, number>;
}

export interface CiRow {
  label: string;
  delta: number | null;
  lo: number | null;
  hi: number | null;
  note?: string;
}

export interface EventTypeAblation {
  n: number;
  ic_fine_vs_dir3: IcResult;
  abnormal_fine: { n: number; mean_abnormal: number | null; std_abnormal: number | null; t_stat: number | null };
}

export interface Manifest {
  run_id: string;
  name: string;
  n_gold_strict: number;
  n_oos_events: number;
  model_scores: Record<string, ModelScore>;
  ic_gold_fine_vs_dir3: IcResult;
  ic_pred_fine_vs_dir3: IcResult;
  ic_decay_fine_gold: { horizon: number; ic: number | null; n: number }[];
  abnormal_by_certainty_gold: Record<string, { n: number; mean_abnormal: number | null; t_stat: number }>;
  backtest: {
    run_id: string;
    metrics: Record<string, number>;
    rejections: Record<string, number>;
    n_intents: number;
  };
  h1_verdict: { supported_on_gold: boolean; supported_on_predictions: boolean; note: string };
  provider_note: string;
  // 下一阶段扩展（v1 manifest 无这些键 → 均为可选）
  split_scheme?: string;
  folds_used?: { ids: number[]; skipped_empty: number[] };
  ic_by_fold_gold?: { fold: number; test: string; n: number; ic_fine: number | null; ic_dir3: number | null }[];
  ablation_gold_exploratory?: Record<string, IcResult>;
  ic_by_event_type_gold_exploratory?: Record<string, EventTypeAblation>;
  cost_sensitivity_backtest?: ({ slippage_bps: number } & Record<string, number | null>)[];
}

/** GET /api/experiments/{run_id} 的包装结构（manifest 在嵌套字段中）。 */
export interface ExperimentDetail extends ExperimentSummary {
  config: Record<string, unknown>;
  dataset_hash: string | null;
  code_commit: string | null;
  metrics: Record<string, unknown> | null;
  manifest_uri: string | null;
  manifest: Manifest | null;
}

export interface QualityResult {
  passed: boolean;
  n_assertions: number;
  n_texts: number;
  n_bars: number;
  assertions: { name: string; passed: boolean; n_violations: number; detail: string }[];
}

export interface AuditRow {
  actor: string;
  action: string;
  target: string;
  result: string;
  request_id: string;
  created_at: string;
}
