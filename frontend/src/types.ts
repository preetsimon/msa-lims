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
