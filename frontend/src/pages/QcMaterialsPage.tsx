import { useEffect, useState } from "react";

import { listQcMaterials } from "../api";
import { PageHeader } from "../components/PageHeader";
import type { QcMaterial } from "../types";

export function QcMaterialsPage() {
  const [materials, setMaterials] = useState<QcMaterial[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const load = () =>
    listQcMaterials({ active_only: !showAll })
      .then(setMaterials)
      .catch((e) => setError(String(e)));

  useEffect(() => {
    load();
  }, [showAll]);

  return (
    <main>
      <PageHeader
        title="QC Materials"
        lede="Certified reference materials and blanks used to bracket every batch."
      />

      {error && <p className="page-error">{error}</p>}

      <div className="toolbar">
        <label>
          <input
            type="checkbox"
            checked={showAll}
            onChange={(e) => setShowAll(e.target.checked)}
          />{" "}
          Show inactive
        </label>
        <button type="button" className="btn-sm" onClick={load}>
          Refresh
        </button>
      </div>

      {materials.length === 0 && !error && (
        <p className="empty-state">No QC materials registered.</p>
      )}

      {materials.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Type</th>
                <th>Lot #</th>
                <th>Au Certified (g/t)</th>
                <th>Uncertainty (g/t)</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {materials.map((m) => (
                <tr key={m.id} className={m.is_active ? undefined : "row-inactive"}>
                  <td className="muted">{m.id}</td>
                  <td>{m.name}</td>
                  <td className="muted">{m.qc_type}</td>
                  <td className="muted">{m.lot_number ?? "—"}</td>
                  <td>{m.certified_au_value_g_t ?? "—"}</td>
                  <td className="muted">{m.certified_au_uncertainty_g_t ?? "—"}</td>
                  <td>
                    <span className={`pill pill-${m.is_active ? "ok" : "neutral"}`}>
                      {m.is_active ? "active" : "retired"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
