import { useEffect, useState } from "react";

import { listFluxRecipes } from "../api";
import type { FluxRecipe } from "../types";

export function FluxRecipesPage() {
  const [recipes, setRecipes] = useState<FluxRecipe[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const load = () => listFluxRecipes().then(setRecipes).catch((e) => setError(String(e)));

  useEffect(() => {
    load();
  }, [showAll]);

  return (
    <div style={{ padding: 24 }}>
      <h1>Flux Recipes</h1>
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
            <th style={{ textAlign: "left", padding: 8 }}>Matrix</th>
            <th style={{ textAlign: "left", padding: 8 }}>Portion (g)</th>
            <th style={{ textAlign: "left", padding: 8 }}>Litharge</th>
            <th style={{ textAlign: "left", padding: 8 }}>Soda Ash</th>
            <th style={{ textAlign: "left", padding: 8 }}>Borax</th>
            <th style={{ textAlign: "left", padding: 8 }}>Silica</th>
            <th style={{ textAlign: "left", padding: 8 }}>Flour</th>
            <th style={{ textAlign: "left", padding: 8 }}>Nitre</th>
            <th style={{ textAlign: "left", padding: 8 }}>Active</th>
          </tr>
        </thead>
        <tbody>
          {recipes.map((r) => (
            <tr key={r.id} style={{ borderBottom: "1px solid #eee", opacity: r.is_active ? 1 : 0.6 }}>
              <td style={{ padding: 8 }}>{r.id}</td>
              <td style={{ padding: 8 }}>{r.name}</td>
              <td style={{ padding: 8 }}>{r.matrix_type}</td>
              <td style={{ padding: 8 }}>{r.nominal_portion_g}</td>
              <td style={{ padding: 8 }}>{r.litharge_g}</td>
              <td style={{ padding: 8 }}>{r.soda_ash_g}</td>
              <td style={{ padding: 8 }}>{r.borax_g}</td>
              <td style={{ padding: 8 }}>{r.silica_g}</td>
              <td style={{ padding: 8 }}>{r.flour_g}</td>
              <td style={{ padding: 8 }}>{r.nitre_g}</td>
              <td style={{ padding: 8 }}>{r.is_active ? "Yes" : "No"}</td>
            </tr>
          ))}
          {recipes.length === 0 && (
            <tr>
              <td colSpan={11} style={{ padding: 16, textAlign: "center", color: "#888" }}>
                No flux recipes
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
