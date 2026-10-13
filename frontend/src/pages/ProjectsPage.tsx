import { useEffect, useState } from "react";

import { listClients, listProjects, type ClientListItem, type Project } from "../api";
import { PageHeader } from "../components/PageHeader";

export function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [clients, setClients] = useState<ClientListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [clientId, setClientId] = useState<string>("");

  useEffect(() => {
    listClients().then(setClients).catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    const params: { client_id?: number } = {};
    if (clientId) params.client_id = Number(clientId);
    listProjects(params)
      .then((data) => {
        if (!cancelled) setProjects(data);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [clientId]);

  const clientName = (id: number) => clients.find((c) => c.id === id)?.name ?? `Client #${id}`;

  return (
    <main>
      <PageHeader title="Projects" lede="Client projects a submission's samples belong to." />

      {error && <p className="page-error">{error}</p>}

      <div className="toolbar">
        <label htmlFor="client-filter">Client</label>
        <select id="client-filter" value={clientId} onChange={(e) => setClientId(e.target.value)}>
          <option value="">All clients</option>
          {clients.map((c) => (
            <option key={c.id} value={c.id}>
              {c.code} — {c.name}
            </option>
          ))}
        </select>
      </div>

      {projects.length === 0 && !error && <p className="empty-state">No projects registered.</p>}

      {projects.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Client</th>
                <th>Location</th>
                <th>Start</th>
                <th>End</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => (
                <tr key={p.id}>
                  <td>{p.name}</td>
                  <td className="muted">{clientName(p.client_id)}</td>
                  <td className="muted">{p.location ?? "—"}</td>
                  <td>{p.start_date ?? "—"}</td>
                  <td>{p.end_date ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
