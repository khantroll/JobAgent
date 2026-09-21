import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch, Job } from "../api";

export default function Review() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState("");

  function load() {
    apiFetch<{ jobs: Job[] }>("/jobs?review_only=true&limit=100")
      .then((r) => setJobs(r.jobs))
      .catch((e) => setError(String(e)));
  }

  useEffect(() => {
    load();
  }, []);

  async function setStatus(id: string, status: string) {
    try {
      await apiFetch(`/jobs/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      load();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1>Review queue</h1>
        <span className="muted">{jobs.length} jobs</span>
      </div>
      <p className="muted">
        Hybrid or borderline commute matches. Skip or keep in review; open a job for details and the listing URL.
      </p>

      {error && <div className="banner banner-error">{error}</div>}

      <div className="review-list">
        {jobs.map((j) => (
          <article key={j.id} className="card review-card">
            <div className="review-head">
              <div>
                <h2>
                  <Link to={`/jobs/${j.id}`}>{j.title}</Link>
                </h2>
                <p className="muted">
                  {j.company} · {j.location || "Remote/unknown"} · score {j.score}
                </p>
              </div>
              <div className="review-actions">
                <button type="button" className="btn-ghost" onClick={() => setStatus(j.id, "skipped")}>
                  Skip
                </button>
                <a href={j.url} target="_blank" rel="noreferrer" className="btn-secondary">
                  Open listing
                </a>
              </div>
            </div>
            {j.commute_note && <p className="commute-note">{j.commute_note}</p>}
            {j.score_reason && <p className="score-reason">{j.score_reason}</p>}
          </article>
        ))}
        {jobs.length === 0 && <p className="empty">No jobs waiting for review.</p>}
      </div>
    </div>
  );
}
