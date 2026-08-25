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
