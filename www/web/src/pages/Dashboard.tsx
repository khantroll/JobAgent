import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch, CycleRun, Stats } from "../api";

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [activeRun, setActiveRun] = useState<CycleRun | null>(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);

  const load = useCallback(async () => {
    const [s, latest] = await Promise.all([
      apiFetch<Stats>("/stats"),
      apiFetch<{ run: CycleRun | null; running: boolean }>("/runs/latest"),
    ]);
    setStats(s);
    setActiveRun(latest.run);
  }, []);

  useEffect(() => {
    load().catch((e) => setError(String(e)));
  }, [load]);

  useEffect(() => {
    if (!activeRun || activeRun.status !== "running") return;
    const t = setInterval(async () => {
      try {
        const run = await apiFetch<CycleRun>(`/runs/${activeRun.id}`);
        setActiveRun(run);
        if (run.status !== "running") load();
      } catch {
        /* ignore poll errors */
      }
    }, 3000);
    return () => clearInterval(t);
  }, [activeRun, load]);

  async function runNow() {
    setStarting(true);
    setError("");
    try {
      const res = await apiFetch<{ run: CycleRun }>("/runs", { method: "POST" });
      setActiveRun(res.run);
    } catch (e) {
      setError(String(e));
    } finally {
      setStarting(false);
    }
  }

  if (!stats) {
    return <div className="page">{error || "Loading dashboard…"}</div>;
  }

  const review = stats.by_status["needs_review"] || 0;
  const applied = stats.by_status["applied"] || 0;
  const newJobs = stats.by_status["new"] || 0;

  return (
    <div className="page">
      <div className="page-header">
        <h1>Dashboard</h1>
        <button
          type="button"
          className="btn-primary"
          disabled={starting || activeRun?.status === "running"}
          onClick={runNow}
        >
          {activeRun?.status === "running" ? "Cycle running…" : "Run search now"}
        </button>
      </div>

      {stats.dry_run && (
        <div className="banner banner-warn">
          Dry run is ON — applications are simulated. Change <code>dry_run</code> in profile.yaml to go live.
        </div>
      )}

      {error && <div className="banner banner-error">{error}</div>}

      {activeRun && (
        <div className={`banner ${activeRun.status === "failed" ? "banner-error" : "banner-info"}`}>
          <strong>Cycle {activeRun.status}</strong>
          {activeRun.summary && (
            <span>
              {" "}
              — {activeRun.summary.new_listings} new, {activeRun.summary.applied} applied,{" "}
              {activeRun.summary.flagged_review} for review
            </span>
          )}
          {activeRun.error && <span> — {activeRun.error}</span>}
        </div>
      )}

      <div className="stat-grid">
        <StatCard label="Total jobs" value={stats.total} />
        <StatCard label="Awaiting score" value={stats.unscored} />
        <StatCard label="New (pipeline)" value={newJobs} />
        <StatCard label="Needs review" value={review} href="/review" />
        <StatCard label="Applied" value={applied} />
        <StatCard label="Min match score" value={stats.min_match_score} />
      </div>

      {stats.last_run && (
        <section className="card">
          <h2>Last logged cycle</h2>
          <p className="muted">
            {new Date(stats.last_run.run_at).toLocaleString()} — {stats.last_run.new} new,{" "}
            {stats.last_run.applied} applied, {stats.last_run.flagged} flagged
          </p>
          <Link to="/runs">View all runs →</Link>
        </section>
      )}
    </div>
  );
}

function StatCard({
  label,
  value,
  href,
}: {
  label: string;
  value: number;
  href?: string;
}) {
  const inner = (
    <>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </>
  );
  return href ? (
    <Link to={href} className="stat-card stat-card-link">
      {inner}
    </Link>
  ) : (
    <div className="stat-card">{inner}</div>
  );
}
