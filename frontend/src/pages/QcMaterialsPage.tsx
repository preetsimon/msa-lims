import { useEffect, useState } from "react";

import { listQcMaterials } from "../api";
import type { QcMaterial } from "../types";

export function QcMaterialsPage() {
  const [materials, setMaterials] = useState<QcMaterial[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    listQcMaterials().then(setMaterials).catch((e) => setError(String(e)));
  }, [showAll]);

  const load = () => listQcMaterials().then(setMaterials).catch((e) => setError(String(e)));

  return (
    <div style={{ padding: 24 }}>
      <h1>QC Materials</h1>
      {error && <div className="page-error">{error}</div>}

      <div style={{ marginBottom: 16, display: "flex", gap: 12, alignItems: "center" }}>
        <label>
          <input
            type="checkbox"
            checked={showAll}
            onChange={(e) => setShowAll(e.target.checked)}
          />{" "}
          Show inactive
        </label>
        <button onClick={load}>Refresh</button>
      </div>

      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ borderBottom: "2px solid #ddd" }}>
            <th style={{ textAlign: "left", padding: 8 }}>ID</th>
            <th style={{ textAlign: "left", padding: 8 }}>Name</th>
            <th style={{ textAlign: "left", padding: 8 }}>Type</th>
            <th style={{ textAlign: "left", padding: 8 }}>Lot #</th>
            <th style={{ textAlign: "left", padding: 8 }}>Au Certified (g/t)</th>
            <th style={{ textAlign: "left", padding: 8 }}>Uncertainty (g/t)</th>
            <th style={{ textAlign: "left", padding: 8 }}>Active</th>
          </tr>
        </thead>
        <tbody>
          {materials.map((m) => (
            <tr key={m.id} style={{ borderBottom: "1px solid #eee", opacity: m.is_active ? 1 : 0.6 }}>
              <td style={{ padding: 8 }}>{m.id}</td>
              <td style={{ padding: 8 }}>{m.name}</td>
              <td style={{ padding: 8 }}>{m.qc_type}</td>
              <td style={{ padding: 8 }}>{m.lot_number ?? "—"}</td>
              <td style={{ padding: 8 }}>{m.certified_au_value_g_t ?? "—"}</td>
              <td style={{ padding: 8 }}>{m.certified_au_uncertainty_g_t ?? "—"}</td>
              <td style={{ padding: 8 }}>{m.is_active ? "Yes" : "No"}</td>
            </tr>
          ))}
          {materials.length === 0 && (
            <tr>
              <td colSpan={7} style={{ padding: 16, textAlign: "center", color: "#888" }}>
                No QC materials
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
