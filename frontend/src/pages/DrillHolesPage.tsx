import { useEffect, useState } from "react";

import { listDrillHoles, type DrillHole } from "../api";

export function DrillHolesPage() {
  const [holes, setHoles] = useState<DrillHole[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = () => listDrillHoles().then(setHoles).catch((e) => setError(String(e)));

  useEffect(() => {
    load();
  }, []);

  return (
    <div style={{ padding: 24 }}>
      <h1>Drill Holes</h1>
      {error && <div className="page-error">{error}</div>}

      <button onClick={load} style={{ marginBottom: 16 }}>Refresh</button>

      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ borderBottom: "2px solid #ddd" }}>
            <th style={{ textAlign: "left", padding: 8 }}>ID</th>
            <th style={{ textAlign: "left", padding: 8 }}>Hole ID</th>
            <th style={{ textAlign: "left", padding: 8 }}>Project</th>
            <th style={{ textAlign: "left", padding: 8 }}>Easting</th>
            <th style={{ textAlign: "left", padding: 8 }}>Northing</th>
            <th style={{ textAlign: "left", padding: 8 }}>Elev (m)</th>
            <th style={{ textAlign: "left", padding: 8 }}>Depth (m)</th>
            <th style={{ textAlign: "left", padding: 8 }}>Dip</th>
            <th style={{ textAlign: "left", padding: 8 }}>Azimuth</th>
            <th style={{ textAlign: "left", padding: 8 }}>Method</th>
          </tr>
        </thead>
        <tbody>
          {holes.map((h) => (
            <tr key={h.id} style={{ borderBottom: "1px solid #eee" }}>
              <td style={{ padding: 8 }}>{h.id}</td>
              <td style={{ padding: 8 }}>{h.hole_id}</td>
              <td style={{ padding: 8 }}>{h.project_id}</td>
              <td style={{ padding: 8 }}>{h.easting ?? "—"}</td>
              <td style={{ padding: 8 }}>{h.northing ?? "—"}</td>
              <td style={{ padding: 8 }}>{h.elevation_m ?? "—"}</td>
              <td style={{ padding: 8 }}>{h.total_depth_m ?? "—"}</td>
              <td style={{ padding: 8 }}>{h.dip_degrees ?? "—"}</td>
              <td style={{ padding: 8 }}>{h.azimuth_degrees ?? "—"}</td>
              <td style={{ padding: 8 }}>{h.drilling_method ?? "—"}</td>
            </tr>
          ))}
          {holes.length === 0 && (
            <tr>
              <td colSpan={10} style={{ padding: 16, textAlign: "center", color: "#888" }}>
                No drill holes
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
