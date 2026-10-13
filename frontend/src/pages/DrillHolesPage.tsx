import { useEffect, useState } from "react";

import { listDrillHoles, listProjects, type DrillHole, type Project } from "../api";
import { PageHeader } from "../components/PageHeader";

export function DrillHolesPage() {
  const [holes, setHoles] = useState<DrillHole[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    const params: { project_id?: number } = {};
    if (projectId) params.project_id = Number(projectId);
    listDrillHoles(params)
      .then(setHoles)
      .catch((e) => setError(String(e)));
  };

  useEffect(() => {
    listProjects().then(setProjects).catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [projectId]);

  const projectName = (id: number) => projects.find((p) => p.id === id)?.name ?? `Project #${id}`;

  return (
    <main>
      <PageHeader
        title="Drill Holes"
        lede="Registered holes a submission's samples can be tied back to."
      />

      {error && <p className="page-error">{error}</p>}

      <div className="toolbar">
        <label htmlFor="project-filter">Project</label>
        <select
          id="project-filter"
          value={projectId}
          onChange={(e) => setProjectId(e.target.value)}
        >
          <option value="">All projects</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>

      {holes.length === 0 && !error && <p className="empty-state">No drill holes registered.</p>}

      {holes.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Hole ID</th>
                <th>Project</th>
                <th>Easting</th>
                <th>Northing</th>
                <th>Elev (m)</th>
                <th>Depth (m)</th>
                <th>Dip</th>
                <th>Azimuth</th>
                <th>Method</th>
              </tr>
            </thead>
            <tbody>
              {holes.map((h) => (
                <tr key={h.id}>
                  <td>{h.hole_id}</td>
                  <td className="muted">{projectName(h.project_id)}</td>
                  <td className="muted">{h.easting ?? "—"}</td>
                  <td className="muted">{h.northing ?? "—"}</td>
                  <td>{h.elevation_m ?? "—"}</td>
                  <td>{h.total_depth_m ?? "—"}</td>
                  <td>{h.dip_degrees ?? "—"}</td>
                  <td>{h.azimuth_degrees ?? "—"}</td>
                  <td className="muted">{h.drilling_method ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
