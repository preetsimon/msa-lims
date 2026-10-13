import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { getDashboardStats, type DashboardStats } from "../api";
import { PageHeader } from "../components/PageHeader";

const STATUS_LABELS: Record<string, string> = {
  received: "Received",
  ready_for_assay: "Ready for Assay",
  in_assay: "In Assay",
  result_entered: "Result Entered",
  approved: "Approved",
  reported: "Reported",
};

/** The status ladder reads left-to-right through the workflow, so the
 * tone climbs with it: neutral at intake, amber while a sample is moving,
 * blue once a result exists, green once it is signed off. */
const STATUS_TONE: Record<string, string> = {
  received: "tone-neutral",
  ready_for_assay: "tone-warn",
  in_assay: "tone-info",
  result_entered: "tone-info",
  approved: "tone-ok",
  reported: "tone-ok",
};

export function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getDashboardStats()
      .then(setStats)
      .catch((e) => setError(String(e)));
  }, []);

  if (error) {
    return (
      <main>
        <p className="page-error">{error}</p>
      </main>
    );
  }

  if (!stats) {
    return (
      <main>
        <p className="muted">Loading…</p>
      </main>
    );
  }

  return (
    <main>
      <PageHeader title="Dashboard" lede="The lab at a glance." />

      <div className="stat-grid">
        <StatCard label="Samples" value={stats.total_samples} tone="tone-info" />
        <StatCard label="Clients" value={stats.total_clients} />
        <StatCard label="Submissions" value={stats.total_submissions} />
        <StatCard label="Batches" value={stats.total_batches} />
        <StatCard label="Certificates" value={stats.total_certificates} tone="tone-ok" />
      </div>

      <h2>Samples by Status</h2>
      <div className="stat-grid">
        {Object.entries(STATUS_LABELS).map(([key, label]) => (
          <StatCard
            key={key}
            label={label}
            value={stats.samples_by_status[key] ?? 0}
            tone={STATUS_TONE[key]}
          />
        ))}
      </div>

      <h2>Quick Links</h2>
      <div className="quick-links">
        <Link to="/samples">View Samples</Link>
        <Link to="/submissions">View Submissions</Link>
        <Link to="/batches">View Batches</Link>
        <Link to="/certificates">View Certificates</Link>
        <Link to="/instruments">Manage Instruments</Link>
        <Link to="/clients">Manage Clients</Link>
      </div>
    </main>
  );
}

function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: string | undefined;
}) {
  return (
    <div className={`stat-card ${tone ?? "tone-neutral"}`}>
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}
