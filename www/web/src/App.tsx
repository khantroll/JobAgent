import { useEffect, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { apiFetch, getToken, setToken } from "./api";
import Dashboard from "./pages/Dashboard";
import Jobs from "./pages/Jobs";
import Review from "./pages/Review";
import JobDetail from "./pages/JobDetail";
import Runs from "./pages/Runs";

export default function App() {
  const [authRequired, setAuthRequired] = useState<boolean | null>(null);
  const [tokenInput, setTokenInput] = useState(getToken());

  useEffect(() => {
    apiFetch<{ auth_required: boolean }>("/health")
      .then((h) => setAuthRequired(h.auth_required))
      .catch(() => setAuthRequired(false));
  }, []);

  const needsLogin =
    authRequired === true && !getToken();

  if (authRequired === null) {
    return <div className="page loading">Loading…</div>;
  }

  if (needsLogin) {
    return (
      <div className="auth-page">
        <div className="auth-card">
          <h1>Job Agent</h1>
          <p>Enter the API token configured on the server (<code>JOB_AGENT_API_TOKEN</code>).</p>
          <input
            type="password"
            placeholder="Bearer token"
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
          />
          <button
            type="button"
            onClick={() => {
              setToken(tokenInput.trim());
              window.location.reload();
            }}
          >
            Continue
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <Link to="/" className="brand">
          Job Agent
        </Link>
        <nav>
          <Link to="/">Dashboard</Link>
          <Link to="/jobs">Jobs</Link>
          <Link to="/review">Review</Link>
          <Link to="/runs">Runs</Link>
        </nav>
        {authRequired && (
          <button
            type="button"
            className="btn-ghost"
            onClick={() => {
              setToken("");
              window.location.reload();
            }}
          >
            Sign out
          </button>
        )}
      </header>
      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/review" element={<Review />} />
          <Route path="/jobs/:id" element={<JobDetail />} />
          <Route path="/runs" element={<Runs />} />
        </Routes>
      </main>
    </div>
  );
}
