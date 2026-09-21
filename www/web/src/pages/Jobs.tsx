import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch, Job } from "../api";

const STATUSES = ["", "new", "needs_review", "skipped", "applied"];
const SORT_FIELDS = [
  { value: "found_at", label: "Discovered" },
  { value: "applied_at", label: "Applied date" },
  { value: "posted_at", label: "Posted date" },
  { value: "status", label: "Status" },
  { value: "score", label: "Match score" },
  { value: "title", label: "Title" },
  { value: "company", label: "Company" },
];

export default function Jobs() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [sortBy, setSortBy] = useState("found_at");
  const [sortDir, setSortDir] = useState("desc");
  const [error, setError] = useState("");

  useEffect(() => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (q) params.set("q", q);
    params.set("sort_by", sortBy);
    params.set("sort_dir", sortDir);
    params.set("limit", "100");
    apiFetch<{ jobs: Job[]; total: number }>(`/jobs?${params}`)
      .then((r) => {
        setJobs(r.jobs);
        setTotal(r.total);
      })
      .catch((e) => setError(String(e)));
  }, [status, q, sortBy, sortDir]);

  return (
    <div className="page">
      <div className="page-header">
        <h1>Jobs</h1>
        <span className="muted">{total} total</span>
      </div>

      <div className="filters">
        <input
          type="search"
          placeholder="Search title, company, location…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          {STATUSES.map((s) => (
            <option key={s || "all"} value={s}>
              {s || "All statuses"}
            </option>
          ))}
        </select>
        <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
          {SORT_FIELDS.map((f) => (
            <option key={f.value} value={f.value}>
              Sort: {f.label}
            </option>
          ))}
        </select>
        <select value={sortDir} onChange={(e) => setSortDir(e.target.value)}>
          <option value="desc">Newest / highest first</option>
          <option value="asc">Oldest / lowest first</option>
        </select>
      </div>

      {error && <div className="banner banner-error">{error}</div>}

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Score</th>
              <th>Title</th>
              <th>Company</th>
              <th>Source</th>
              <th>Status</th>
              <th>Applied</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>{j.score ?? "—"}</td>
                <td>
                  <Link to={`/jobs/${j.id}`}>{j.title}</Link>
                </td>
                <td>{j.company}</td>
                <td>{j.source || "—"}</td>
                <td>
                  <span className={`pill pill-${j.status}`}>{j.status}</span>
                </td>
                <td>{j.applied_at ? new Date(j.applied_at).toLocaleDateString() : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {jobs.length === 0 && <p className="empty">No jobs match filters.</p>}
      </div>
    </div>
  );
}
