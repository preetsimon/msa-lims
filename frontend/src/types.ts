/**
 * Types mirroring the API's response shapes.
 *
 * Hand-written for now. Once the API surface stops moving these should be
 * generated from `/openapi.json` — a hand-kept copy of a schema drifts, and a
 * drifted type is worse than no type because it is believed.
 */

export type ComponentStatus = "ok" | "unavailable" | "not_configured";

export interface ComponentHealth {
  status: ComponentStatus;
  detail: string | null;
}

export interface HealthResponse {
  status: "healthy" | "degraded" | "unhealthy";
  version: string;
  database: ComponentHealth;
  qc_sentinel: ComponentHealth;
}

/**
 * A result as the API renders it.
 *
 * `value` is null exactly when `censored` is true — the wire format keeps the
 * same distinction the Python domain does, so a non-detect cannot arrive here
 * as a zero. Render with {@link formatMeasured} rather than reading `value`
 * directly.
 */
export interface MeasuredValue {
  value: string | null;
  detection_limit: string | null;
  censored: boolean;
  unit: string;
}

export function formatMeasured(measured: MeasuredValue): string {
  return measured.censored
    ? `<${measured.detection_limit} ${measured.unit}`
    : `${measured.value} ${measured.unit}`;
}

/** One row of `GET /api/samples` — deliberately lean; see the backend's own
 * `SampleListItemOut` docstring for why no grade or certificate list rides
 * along here. */
export interface SampleListItem {
  id: number;
  sample_id: string;
  sample_type: string;
  status: string;
  client_name: string;
  submission_number: string;
}

/** One certificate that names a sample — not the document itself, just
 * enough to link to `GET /api/certificates/{id}` or download its PDF. */
export interface CertificateReference {
  id: number;
  certificate_number: string;
}

export interface FireAssayResult {
  id: number;
  sample_id: number;
  method: string;
  gold_bead_mg: string;
  sample_weight_g: string;
  balance_sensitivity_mg: string | null;
  au: MeasuredValue;
  analysed_at: string;
  supersedes_id: number | null;
  superseded_reason: string | null;
  notes: string | null;
  crucible_id: number | null;
}

export interface SampleDetail {
  id: number;
  sample_id: string;
  sample_type: string;
  status: string;
  submission_id: number;
  drill_hole_id: number | null;
  from_depth_m: string | null;
  to_depth_m: string | null;
  current_result: FireAssayResult | null;
  certificates: CertificateReference[];
}

/** One row of `GET /api/batches` — status and timing only, matching
 * `SampleListItem`'s lean-list precedent. */
export interface Batch {
  id: number;
  batch_number: string;
  status: string;
  opened_by_id: number;
  opened_at: string;
  notes: string | null;
}

/**
 * One crucible as it sits in a batch's tray.
 *
 * `sample_id`/`qc_material_id` are mutually exclusive, mirroring the
 * database's own CHECK constraint — exactly one of each pair is non-null.
 * `sample_label`/`qc_material_name` carry the human-readable identity
 * alongside the id, so the tray never has to fetch a second time just to
 * label a slot.
 */
export interface CrucibleSlot {
  id: number;
  position_row: number;
  position_col: number;
  status: string;
  sample_id: number | null;
  sample_label: string | null;
  qc_material_id: number | null;
  qc_material_name: string | null;
  qc_material_type: string | null;
}

/** `GET /api/batches/{id}` — a batch and the furnace tray its crucibles sit
 * in. `furnace_rows`/`furnace_columns` are a single lab-wide setting today,
 * not per-batch — see PROGRESS.md's open questions — carried here because
 * this is the response a tray view actually renders from. */
export interface BatchDetail {
  id: number;
  batch_number: string;
  status: string;
  opened_by_id: number;
  opened_at: string;
  notes: string | null;
  furnace_rows: number;
  furnace_columns: number;
  crucibles: CrucibleSlot[];
}

/** A registered flux recipe — the picker a charge form draws from. */
export interface FluxRecipe {
  id: number;
  name: string;
  matrix_type: string;
  nominal_portion_g: string;
  litharge_g: string;
  soda_ash_g: string;
  borax_g: string;
  silica_g: string;
  flour_g: string;
  nitre_g: string;
  is_active: boolean;
}

/** A registered QC material (CRM or blank) — the picker a QC charge form
 * draws from. `certified_au_value_g_t`/`certified_au_uncertainty_g_t` are
 * null for a blank, which is defined by carrying no certified grade. */
export interface QcMaterial {
  id: number;
  name: string;
  qc_type: string;
  lot_number: string | null;
  certified_au_value_g_t: string | null;
  certified_au_uncertainty_g_t: string | null;
  is_active: boolean;
}

/** The full crucible row `POST /api/batches/{id}/crucibles` and the
 * parting/weighing endpoints return — every flux amount and measurement
 * column, unlike the tray's own lean `CrucibleSlot`. Nothing here renders
 * this shape directly; a batch detail refetch after each write is what the
 * tray actually redraws from. */
export interface Crucible {
  id: number;
  batch_id: number;
  sample_id: number | null;
  qc_material_id: number | null;
  flux_recipe_id: number;
  position_row: number;
  position_col: number;
  status: string;
  sample_weight_g: string;
  litharge_g: string;
  soda_ash_g: string;
  borax_g: string;
  silica_g: string;
  flour_g: string;
  nitre_g: string;
  lead_button_weight_mg: string | null;
  prill_weight_mg: string | null;
  parting_acid_volume_ml: string | null;
  parted_at: string | null;
  gold_bead_mg: string | null;
  weighed_at: string | null;
  charged_at: string;
  notes: string | null;
}
