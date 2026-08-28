/**
 * Typed fetch helpers over the API the Vite dev server proxies to :8002.
 *
 * No auth headers are sent — `MSA_AUTH_MODE=dev_headers` (this stack's local
 * default) resolves an unheadered request to the least-privileged role, the
 * same way every curl check in PROGRESS.md's live verifications does.
 */

import type {
  Batch,
  BatchDetail,
  Crucible,
  FluxRecipe,
  Provenance,
  QcMaterial,
  SampleDetail,
  SampleListItem,
} from "./types";

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** Domain refusals come back as `{"detail": "<message>"}` — surface that
 * message directly rather than the raw JSON text, falling back to it for
 * whatever isn't shaped that way (a 404 from a route with no handler, say). */
async function errorMessage(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const body: unknown = JSON.parse(text);
    if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // Not JSON — fall through to the raw text below.
  }
  return text || response.statusText;
}

async function getJSON<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response));
  }
  return response.json() as Promise<T>;
}

async function sendJSON<T>(method: "POST" | "PATCH", path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response));
  }
  return response.json() as Promise<T>;
}

export function listSamples(params?: { status?: string; client_id?: number }): Promise<SampleListItem[]> {
  const queryParts: string[] = [];
  if (params?.status) queryParts.push(`status=${encodeURIComponent(params.status)}`);
  if (params?.client_id !== undefined) queryParts.push(`client_id=${params.client_id}`);
  const query = queryParts.length > 0 ? `?${queryParts.join("&")}` : "";
  return getJSON<SampleListItem[]>(`/api/samples${query}`);
}

export function getSample(id: number): Promise<SampleDetail> {
  return getJSON<SampleDetail>(`/api/samples/${id}`);
}

export function listBatches(): Promise<Batch[]> {
  return getJSON<Batch[]>("/api/batches");
}

export function getBatch(id: number): Promise<BatchDetail> {
  return getJSON<BatchDetail>(`/api/batches/${id}`);
}

export function listFluxRecipes(): Promise<FluxRecipe[]> {
  return getJSON<FluxRecipe[]>("/api/flux-recipes");
}

export function listQcMaterials(): Promise<QcMaterial[]> {
  return getJSON<QcMaterial[]>("/api/qc-materials");
}

export interface ClientListItem {
  id: number;
  code: string;
  name: string;
  is_active: boolean;
  submission_count: number;
}

export function listClients(): Promise<ClientListItem[]> {
  return getJSON<ClientListItem[]>("/api/clients");
}

/** Exactly one of `sample_id`/`qc_material_id`, mirroring the API's own
 * either/or CHECK constraint — the form that builds this must enforce the
 * same rule client-side, but the server is what actually decides. */
export interface ChargeCrucibleRequest {
  sample_id: number | null;
  qc_material_id: number | null;
  flux_recipe_id: number;
  position_row: number;
  position_col: number;
  sample_weight_g: string;
  charged_at: string;
  notes: string | null;
}

export function chargeCrucible(batchId: number, body: ChargeCrucibleRequest): Promise<Crucible> {
  return sendJSON<Crucible>("POST", `/api/batches/${batchId}/crucibles`, body);
}

export interface PartCrucibleRequest {
  lead_button_weight_mg: string;
  prill_weight_mg: string;
  parting_acid_volume_ml: string;
  parted_at: string;
}

export function partCrucible(
  batchId: number,
  crucibleId: number,
  body: PartCrucibleRequest,
): Promise<Crucible> {
  return sendJSON<Crucible>(
    "POST",
    `/api/batches/${batchId}/crucibles/${crucibleId}/parting`,
    body,
  );
}

export interface WeighCrucibleRequest {
  gold_bead_mg: string;
  weighed_at: string;
}

export function weighCrucible(
  batchId: number,
  crucibleId: number,
  body: WeighCrucibleRequest,
): Promise<Crucible> {
  return sendJSON<Crucible>(
    "POST",
    `/api/batches/${batchId}/crucibles/${crucibleId}/weighing`,
    body,
  );
}

export function advanceBatchStatus(batchId: number, targetStatus: string): Promise<Batch> {
  return sendJSON<Batch>("PATCH", `/api/batches/${batchId}/status`, { status: targetStatus });
}

export function getSampleProvenance(id: number): Promise<Provenance> {
  return getJSON<Provenance>(`/api/samples/${id}/provenance`);
}

// ---------------------------------------------------------------------------
// Multi-element ICP results
// ---------------------------------------------------------------------------

export interface MultiElementResultItem {
  element: string;
  grade_value: string;
  grade_unit: string;
  detection_limit: string | null;
}

export interface MultiElementImportRequest {
  sample_id: number;
  digest_method: string;
  method_notes: string | null;
  analysed_at: string;
  results: MultiElementResultItem[];
}

export interface MultiElementResultOut {
  id: number;
  sample_id: number;
  element: string;
  grade_value: string;
  grade_unit: string;
  detection_limit: string | null;
  digest_method: string;
  method_notes: string | null;
  analyst_id: number;
  analysed_at: string;
  supersedes_id: number | null;
  superseded_reason: string | null;
  notes: string | null;
  created_at: string;
}

export interface MultiElementImportResponse {
  sample_id: number;
  digest_method: string;
  analysed_at: string;
  imported: MultiElementResultOut[];
}

export function importMultiElementResults(
  sampleId: number,
  body: MultiElementImportRequest,
): Promise<MultiElementImportResponse> {
  return sendJSON<MultiElementImportResponse>(
    "POST",
    `/api/samples/${sampleId}/multi-element-results`,
    body,
  );
}

export function listMultiElementResults(sampleId: number): Promise<MultiElementResultOut[]> {
  return getJSON<MultiElementResultOut[]>(`/api/samples/${sampleId}/multi-element-results`);
}

export { ApiError };
