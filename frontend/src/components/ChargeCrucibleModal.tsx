import { useEffect, useState } from "react";

import { ApiError, chargeCrucible, listFluxRecipes, listQcMaterials, listSamples } from "../api";
import { datetimeLocalValueToIso, nowAsDatetimeLocalValue } from "../datetimeInput";
import type { FluxRecipe, QcMaterial, SampleListItem } from "../types";
import { Modal } from "./Modal";

type Kind = "sample" | "qc";

/**
 * Charges one crucible into an empty tray slot. Mirrors
 * `CrucibleChargeCreate` exactly: a sample or a QC material, never both —
 * the toggle below enforces that client-side, the server enforces it for
 * real.
 */
export function ChargeCrucibleModal({
  batchId,
  positionRow,
  positionCol,
  onClose,
  onCharged,
}: {
  batchId: number;
  positionRow: number;
  positionCol: number;
  onClose: () => void;
  onCharged: () => void;
}) {
  const [samples, setSamples] = useState<SampleListItem[] | null>(null);
  const [materials, setMaterials] = useState<QcMaterial[] | null>(null);
  const [recipes, setRecipes] = useState<FluxRecipe[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [kind, setKind] = useState<Kind>("sample");
  const [sampleId, setSampleId] = useState("");
  const [qcMaterialId, setQcMaterialId] = useState("");
  const [fluxRecipeId, setFluxRecipeId] = useState("");
  const [sampleWeightG, setSampleWeightG] = useState("");
  const [chargedAt, setChargedAt] = useState(nowAsDatetimeLocalValue());
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      listSamples({ status: "ready_for_assay" }),
      listQcMaterials(),
      listFluxRecipes(),
    ])
      .then(([sampleList, materialList, recipeList]) => {
        if (cancelled) return;
        setSamples(sampleList);
        setMaterials(materialList);
        setRecipes(recipeList);
      })
      .catch(() => {
        if (!cancelled) setLoadError("Could not load samples, materials, or recipes.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const ready = samples !== null && materials !== null && recipes !== null;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitError(null);

    if (kind === "sample" && !sampleId) {
      setSubmitError("Choose a sample.");
      return;
    }
    if (kind === "qc" && !qcMaterialId) {
      setSubmitError("Choose a QC material.");
      return;
    }
    if (!fluxRecipeId) {
      setSubmitError("Choose a flux recipe.");
      return;
    }
    if (!sampleWeightG || Number(sampleWeightG) <= 0) {
      setSubmitError("Portion weight must be greater than zero.");
      return;
    }

    setSubmitting(true);
    try {
      await chargeCrucible(batchId, {
        sample_id: kind === "sample" ? Number(sampleId) : null,
        qc_material_id: kind === "qc" ? Number(qcMaterialId) : null,
        flux_recipe_id: Number(fluxRecipeId),
        position_row: positionRow,
        position_col: positionCol,
        sample_weight_g: sampleWeightG,
        charged_at: datetimeLocalValueToIso(chargedAt),
        notes: notes.trim() || null,
      });
      onCharged();
      onClose();
    } catch (err: unknown) {
      setSubmitError(err instanceof ApiError ? err.message : "Charging the crucible failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`Charge slot ${positionRow}-${positionCol}`} onClose={onClose}>
      {loadError && <p className="error">{loadError}</p>}
      {!ready && !loadError && <p className="muted">Loading…</p>}

      {ready && (
        <form onSubmit={handleSubmit}>
          <div className="form-field">
            <label>Contents</label>
            <div className="radio-row">
              <label>
                <input
                  type="radio"
                  checked={kind === "sample"}
                  onChange={() => setKind("sample")}
                />
                Sample
              </label>
              <label>
                <input type="radio" checked={kind === "qc"} onChange={() => setKind("qc")} />
                QC material
              </label>
            </div>
          </div>

          {kind === "sample" && (
            <div className="form-field">
              <label htmlFor="charge-sample">Sample (ready for assay)</label>
              <select
                id="charge-sample"
                value={sampleId}
                onChange={(event) => setSampleId(event.target.value)}
              >
                <option value="">Choose a sample…</option>
                {samples?.map((sample) => (
                  <option key={sample.id} value={sample.id}>
                    {sample.sample_id} · {sample.client_name}
                  </option>
                ))}
              </select>
              {samples?.length === 0 && (
                <p className="muted">No samples are currently ready for assay.</p>
              )}
            </div>
          )}

          {kind === "qc" && (
            <div className="form-field">
              <label htmlFor="charge-qc">QC material</label>
              <select
                id="charge-qc"
                value={qcMaterialId}
                onChange={(event) => setQcMaterialId(event.target.value)}
              >
                <option value="">Choose a material…</option>
                {materials?.map((material) => (
                  <option key={material.id} value={material.id}>
                    {material.name} · {material.qc_type}
                  </option>
                ))}
              </select>
              {materials?.length === 0 && (
                <p className="muted">No active QC materials are registered.</p>
              )}
            </div>
          )}

          <div className="form-field">
            <label htmlFor="charge-recipe">Flux recipe</label>
            <select
              id="charge-recipe"
              value={fluxRecipeId}
              onChange={(event) => setFluxRecipeId(event.target.value)}
            >
              <option value="">Choose a recipe…</option>
              {recipes?.map((recipe) => (
                <option key={recipe.id} value={recipe.id}>
                  {recipe.name} ({recipe.nominal_portion_g} g nominal)
                </option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="charge-weight">Portion weight (g)</label>
            <input
              id="charge-weight"
              type="number"
              step="any"
              min="0"
              value={sampleWeightG}
              onChange={(event) => setSampleWeightG(event.target.value)}
            />
          </div>

          <div className="form-field">
            <label htmlFor="charge-time">Charged at</label>
            <input
              id="charge-time"
              type="datetime-local"
              value={chargedAt}
              onChange={(event) => setChargedAt(event.target.value)}
            />
          </div>

          <div className="form-field">
            <label htmlFor="charge-notes">Notes (optional)</label>
            <textarea
              id="charge-notes"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              rows={2}
            />
          </div>

          {submitError && <p className="error">{submitError}</p>}

          <div className="form-actions">
            <button type="button" className="btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={submitting}>
              {submitting ? "Charging…" : "Charge crucible"}
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}
