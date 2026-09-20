import { useEffect, useState } from "react";

import { listClients, listProjects, type ClientListItem, type Project } from "../api";

export function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [clients, setClients] = useState<ClientListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [clientId, setClientId] = useState<string>("");

  const load = () => {
    const params: { client_id?: number } = {};
    if (clientId) params.client_id = Number(clientId);
    listProjects(params).then(setProjects).catch((e) => setError(String(e)));
  };

  useEffect(() => {
    listClients().then(setClients).catch(() => {});
    load();
  }, []);

  const clientName = (id: number) => clients.find((c) => c.id === id)?.name ?? `Client #${id}`;

  return (
    <div style={{ padding: 24 }}>
      <h1>Projects</h1>
      {error && <div className="page-error">{error}</div>}

      <div style={{ marginBottom: 16, display: "flex", gap: 8, alignItems: "center" }}>
        <select value={clientId} onChange={(e) => setClientId(e.target.value)}>
          <option value="">All clients</option>
          {clients.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
        <button onClick={load}>Filter</button>
      </div>

      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ borderBottom: "2px solid #ddd" }}>
            <th style={{ textAlign: "left", padding: 8 }}>ID</th>
            <th style={{ textAlign: "left", padding: 8 }}>Name</th>
            <th style={{ textAlign: "left", padding: 8 }}>Client</th>
            <th style={{ textAlign: "left", padding: 8 }}>Location</th>
            <th style={{ textAlign: "left", padding: 8 }}>Start</th>
            <th style={{ textAlign: "left", padding: 8 }}>End</th>
          </tr>
        </thead>
        <tbody>
          {projects.map((p) => (
            <tr key={p.id} style={{ borderBottom: "1px solid #eee" }}>
              <td style={{ padding: 8 }}>{p.id}</td>
              <td style={{ padding: 8 }}>{p.name}</td>
              <td style={{ padding: 8 }}>{clientName(p.client_id)}</td>
              <td style={{ padding: 8 }}>{p.location ?? "—"}</td>
              <td style={{ padding: 8 }}>{p.start_date ?? "—"}</td>
              <td style={{ padding: 8 }}>{p.end_date ?? "—"}</td>
            </tr>
          ))}
          {projects.length === 0 && (
            <tr>
              <td colSpan={6} style={{ padding: 16, textAlign: "center", color: "#888" }}>
                No projects
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
