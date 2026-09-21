import { useEffect, useState } from "react";
import { apiFetch, CycleRun } from "../api";

type LogRun = {
  id: number;
  run_at: string;
  source: string;
  found: number;
  new: number;
  applied: number;
  flagged: number;
  errors: string;
};

export default function Runs() {
  const [apiRuns, setApiRuns] = useState<CycleRun[]>([]);
  const [history, setHistory] = useState<LogRun[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      apiFetch<{ runs: CycleRun[] }>("/runs"),
      apiFetch<{ runs: LogRun[] }>("/runs/history"),
    ])
      .then(([a, h]) => {
        setApiRuns(a.runs);
        setHistory(h.runs);
      })
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="page">
      <h1>Runs</h1>
      {error && <div className="banner banner-error">{error}</div>}

      <section className="card">
        <h2>Triggered from web UI</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Started</th>
                <th>Status</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {apiRuns.map((r) => (
                <tr key={r.id}>
                  <td>{new Date(r.started_at).toLocaleString()}</td>
                  <td>{r.status}</td>
                  <td>
                    {r.summary
                      ? `${r.summary.new_listings} new / ${r.summary.applied} applied`
                      : r.error || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {apiRuns.length === 0 && <p className="empty">No UI-triggered runs yet.</p>}
        </div>
      </section>

      <section className="card">
        <h2>Database run log</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>New</th>
                <th>Applied</th>
                <th>Flagged</th>
              </tr>
            </thead>
            <tbody>
              {history.map((r) => (
                <tr key={r.id}>
                  <td>{new Date(r.run_at).toLocaleString()}</td>
                  <td>{r.new}</td>
                  <td>{r.applied}</td>
                  <td>{r.flagged}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
