const TOKEN_KEY = "job_agent_api_token";

export function getToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(token: string) {
  if (token) sessionStorage.setItem(TOKEN_KEY, token);
  else sessionStorage.removeItem(TOKEN_KEY);
}

function apiBase(): string {
  const env = import.meta.env.VITE_API_BASE as string | undefined;
  if (env) return env.replace(/\/$/, "");
  // Match Vite base (e.g. /jobagent/ on nedragaardkeep.quest)
  const base = (import.meta.env.BASE_URL || "/").replace(/\/$/, "");
  return base ? `${base}/api` : "/api";
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${apiBase()}${path}`, { ...options, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const msg = (body as { detail?: string }).detail || res.statusText;
    throw new Error(msg || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export type Job = {
  id: string;
  title: string;
  company: string;
  location: string | null;
  url: string;
  description: string | null;
  source: string | null;
  score: number | null;
  score_reason: string | null;
  work_type: string | null;
  commute_minutes: number | null;
  commute_note: string | null;
  status: string;
  found_at: string;
  applied_at: string | null;
  resume_path: string | null;
  cover_path: string | null;
};

export type Stats = {
  total: number;
  unscored: number;
  by_status: Record<string, number>;
  dry_run: boolean;
  min_match_score: number;
  last_run: {
    run_at: string;
    new: number;
    applied: number;
    flagged: number;
  } | null;
};

export type CycleRun = {
  id: string;
  status: "running" | "completed" | "failed";
  started_at: string;
  finished_at: string | null;
  summary: {
    new_listings: number;
    applied: number;
    flagged_review: number;
    dry_run: boolean;
  } | null;
  error: string | null;
};
