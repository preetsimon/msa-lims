import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { getDashboardStats, type DashboardStats } from "../api";

const STATUS_LABELS: Record<string, string> = {
  received: "Received",
  ready_for_assay: "Ready for Assay",
  in_assay: "In Assay",
  result_entered: "Result Entered",
  approved: "Approved",
  reported: "Reported",
};

export function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getDashboardStats()
      .then(setStats)
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <div className="page-error">{error}</div>;
  if (!stats) return <div>Loading...</div>;

  const statusColors: Record<string, string> = {
    received: "#6b7280",
    ready_for_assay: "#f59e0b",
    in_assay: "#3b82f6",
    result_entered: "#8b5cf6",
    approved: "#10b981",
    reported: "#059669",
  };

  return (
    <div style={{ padding: 24 }}>
      <h1>Dashboard</h1>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 16, marginBottom: 32 }}>
        <StatCard label="Samples" value={stats.total_samples} />
        <StatCard label="Clients" value={stats.total_clients} />
        <StatCard label="Submissions" value={stats.total_submissions} />
        <StatCard label="Batches" value={stats.total_batches} />
        <StatCard label="Certificates" value={stats.total_certificates} />
      </div>

      <h2>Samples by Status</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 12, marginBottom: 32 }}>
        {Object.entries(STATUS_LABELS).map(([key, label]) => (
          <div
            key={key}
            style={{
              border: `2px solid ${statusColors[key] || "#999"}`,
              borderRadius: 8,
              padding: 16,
              textAlign: "center",
            }}
          >
            <div style={{ fontSize: 28, fontWeight: 700, color: statusColors[key] || "#999" }}>
              {stats.samples_by_status[key] ?? 0}
            </div>
            <div style={{ fontSize: 13, color: "#555" }}>{label}</div>
          </div>
        ))}
      </div>

      <h2>Quick Links</h2>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <Link to="/samples" style={{ padding: "8px 16px", border: "1px solid #ccc", borderRadius: 6 }}>
          View Samples
        </Link>
        <Link to="/submissions" style={{ padding: "8px 16px", border: "1px solid #ccc", borderRadius: 6 }}>
          View Submissions
        </Link>
        <Link to="/batches" style={{ padding: "8px 16px", border: "1px solid #ccc", borderRadius: 6 }}>
          View Batches
        </Link>
        <Link to="/certificates" style={{ padding: "8px 16px", border: "1px solid #ccc", borderRadius: 6 }}>
          View Certificates
        </Link>
        <Link to="/instruments" style={{ padding: "8px 16px", border: "1px solid #ccc", borderRadius: 6 }}>
          Manage Instruments
        </Link>
        <Link to="/clients" style={{ padding: "8px 16px", border: "1px solid #ccc", borderRadius: 6 }}>
          Manage Clients
        </Link>
      </div>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 20, textAlign: "center" }}>
      <div style={{ fontSize: 32, fontWeight: 700 }}>{value}</div>
      <div style={{ fontSize: 14, color: "#555" }}>{label}</div>
    </div>
  );
}
