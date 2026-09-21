import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { apiFetch, Job } from "../api";

export default function JobDetail() {
  const { id } = useParams();
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!id) return;
    apiFetch<Job>(`/jobs/${id}`)
      .then(setJob)
      .catch((e) => setError(String(e)));
  }, [id]);

  async function setStatus(status: string) {
    if (!id) return;
    try {
      const updated = await apiFetch<Job>(`/jobs/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      setJob(updated);
    } catch (e) {
      setError(String(e));
    }
  }

  if (error) return <div className="page banner banner-error">{error}</div>;
  if (!job) return <div className="page">Loading…</div>;

  return (
    <div className="page">
      <Link to="/jobs" className="back-link">
        ← Jobs
      </Link>
      <div className="page-header">
        <h1>{job.title}</h1>
        <span className={`pill pill-${job.status}`}>{job.status}</span>
      </div>
      <p className="subtitle">
        {job.company} · {job.location || "—"} · {job.source}
      </p>

      <div className="detail-grid">
        <section className="card">
          <h2>Match</h2>
          <p>
            <strong>Score:</strong> {job.score ?? "—"}
          </p>
          {job.score_reason && <p>{job.score_reason}</p>}
        </section>
        <section className="card">
          <h2>Commute</h2>
          <p>
            <strong>Type:</strong> {job.work_type || "—"}
          </p>
          {job.commute_minutes != null && <p>{job.commute_minutes} min drive</p>}
          {job.commute_note && <p>{job.commute_note}</p>}
        </section>
      </div>

      <div className="action-row">
        <a href={job.url} target="_blank" rel="noreferrer" className="btn-primary">
          View listing
        </a>
        <button type="button" className="btn-ghost" onClick={() => setStatus("skipped")}>
          Mark skipped
        </button>
        <button type="button" className="btn-secondary" onClick={() => setStatus("needs_review")}>
          Needs review
        </button>
      </div>

      {job.description && (
        <section className="card description">
          <h2>Description</h2>
          <pre>{job.description}</pre>
        </section>
      )}
    </div>
  );
}
