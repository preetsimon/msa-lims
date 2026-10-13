import { useEffect, useState } from "react";

import { listFluxRecipes } from "../api";
import { PageHeader } from "../components/PageHeader";
import type { FluxRecipe } from "../types";

export function FluxRecipesPage() {
  const [recipes, setRecipes] = useState<FluxRecipe[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const load = () =>
    listFluxRecipes({ active_only: !showAll })
      .then(setRecipes)
      .catch((e) => setError(String(e)));

  useEffect(() => {
    load();
  }, [showAll]);

  return (
    <main>
      <PageHeader
        title="Flux Recipes"
        lede="Charge compositions used when fusing a sample in the furnace."
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

      {recipes.length === 0 && !error && (
        <p className="empty-state">No flux recipes registered.</p>
      )}

      {recipes.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Matrix</th>
                <th>Portion (g)</th>
                <th>Litharge</th>
                <th>Soda Ash</th>
                <th>Borax</th>
                <th>Silica</th>
                <th>Flour</th>
                <th>Nitre</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {recipes.map((r) => (
                <tr key={r.id} className={r.is_active ? undefined : "row-inactive"}>
                  <td className="muted">{r.id}</td>
                  <td>{r.name}</td>
                  <td className="muted">{r.matrix_type}</td>
                  <td>{r.nominal_portion_g}</td>
                  <td>{r.litharge_g}</td>
                  <td>{r.soda_ash_g}</td>
                  <td>{r.borax_g}</td>
                  <td>{r.silica_g}</td>
                  <td>{r.flour_g}</td>
                  <td>{r.nitre_g}</td>
                  <td>
                    <span className={`pill pill-${r.is_active ? "ok" : "neutral"}`}>
                      {r.is_active ? "active" : "retired"}
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
