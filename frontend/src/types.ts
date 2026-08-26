/**
 * Type aliases over `generated-types.ts`, the schema `openapi-typescript`
 * derives straight from the live API's own `/openapi.json` — closing audit
 * idea #18. Every export here is a name pointing at a generated schema, not
 * a hand-copied field list: the wire contract can no longer drift silently,
 * because `make generate-types` (or `npm run generate-types`) regenerates
 * `generated-types.ts` from the real app and a CI step fails the build if
 * the regenerated file differs from what's committed.
 *
 * Only two things live here that aren't pure aliases: `formatMeasured`,
 * because rendering a censored value is application logic, not a type; and
 * the friendly names themselves — the generated schemas are named
 * `BatchOut`, `SampleDetailOut`, etc. (FastAPI's own response-model
 * convention), and every page/component in this app imports the shorter
 * name a schema's *use* suggests instead.
 */

import type { components } from "./generated-types";

export type ComponentHealth = components["schemas"]["ComponentHealth"];
export type ComponentStatus = ComponentHealth["status"];
export type HealthResponse = components["schemas"]["HealthResponse"];

/**
 * A result as the API renders it.
 *
 * `value` is null exactly when `censored` is true — the wire format keeps the
 * same distinction the Python domain does, so a non-detect cannot arrive here
 * as a zero. Render with {@link formatMeasured} rather than reading `value`
 * directly.
 */
export type MeasuredValue = components["schemas"]["MeasuredValueOut"];

export function formatMeasured(measured: MeasuredValue): string {
  return measured.censored
    ? `<${measured.detection_limit} ${measured.unit}`
    : `${measured.value} ${measured.unit}`;
}

/** One row of `GET /api/samples` — deliberately lean; see the backend's own
 * `SampleListItemOut` docstring for why no grade or certificate list rides
 * along here. */
export type SampleListItem = components["schemas"]["SampleListItemOut"];

/** One certificate that names a sample — not the document itself, just
 * enough to link to `GET /api/certificates/{id}` or download its PDF. */
export type CertificateReference = components["schemas"]["CertificateReferenceOut"];

export type FireAssayResult = components["schemas"]["FireAssayResultOut"];

export type SampleDetail = components["schemas"]["SampleDetailOut"];

/** One row of `GET /api/batches` — status and timing only, matching
 * `SampleListItem`'s lean-list precedent. */
export type Batch = components["schemas"]["BatchOut"];

/**
 * One crucible as it sits in a batch's tray.
 *
 * `sample_id`/`qc_material_id` are mutually exclusive, mirroring the
 * database's own CHECK constraint — exactly one of each pair is non-null.
 * `sample_label`/`qc_material_name` carry the human-readable identity
 * alongside the id, so the tray never has to fetch a second time just to
 * label a slot.
 */
export type CrucibleSlot = components["schemas"]["CrucibleSlotOut"];

/** `GET /api/batches/{id}` — a batch and the furnace tray its crucibles sit
 * in. `furnace_rows`/`furnace_columns` are a single lab-wide setting today,
 * not per-batch — see PROGRESS.md's open questions — carried here because
 * this is the response a tray view actually renders from. */
export type BatchDetail = components["schemas"]["BatchDetailOut"];

/** A registered flux recipe — the picker a charge form draws from. */
export type FluxRecipe = components["schemas"]["FluxRecipeOut"];

/** A registered QC material (CRM or blank) — the picker a QC charge form
 * draws from. `certified_au_value_g_t`/`certified_au_uncertainty_g_t` are
 * null for a blank, which is defined by carrying no certified grade. */
export type QcMaterial = components["schemas"]["QcMaterialOut"];

/** The full crucible row `POST /api/batches/{id}/crucibles` and the
 * parting/weighing endpoints return — every flux amount and measurement
 * column, unlike the tray's own lean `CrucibleSlot`. Nothing here renders
 * this shape directly; a batch detail refetch after each write is what the
 * tray actually redraws from. */
export type Crucible = components["schemas"]["CrucibleOut"];

/** One sample's whole evidence dossier — `GET /api/samples/{id}/provenance`.
 * Every `Decimal` and timestamp arrives as a string because the `seal` is
 * computed over exactly those bytes; see the backend's own
 * `provenance/service.py` for what the seal covers and how to recompute it. */
export type Provenance = components["schemas"]["ProvenanceOut"];
export type ProvenanceCrucible = components["schemas"]["ProvenanceCrucibleOut"];
export type ProvenanceResult = components["schemas"]["ProvenanceResultOut"];
export type ProvenanceCertificate = components["schemas"]["ProvenanceCertificateOut"];
export type ProvenanceAuditEntry = components["schemas"]["ProvenanceAuditEntryOut"];
