/**
 * Typed fetch helpers over the API the Vite dev server proxies to :8002.
 *
 * Auth headers are provided by the auth module. In dev_headers mode, the
 * ``X-Actor`` and ``X-Actor-Role`` headers identify the caller. In oidc
 * mode, a ``Bearer`` token is sent instead.
 */

import type {
  Batch,
  BatchDetail,
  CertificateListItem,
  Crucible,
  FluxRecipe,
  Provenance,
  QcMaterial,
  SampleDetail,
  SampleListItem,
  Submission,
  SubmissionListItem,
} from "./types";
import type { components } from "./generated-types";

export type Instrument = components["schemas"]["InstrumentOut"];

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

// ── Auth headers ──────────────────────────────────────────────────────
// Set by AuthProvider via setAuthHeaders(); read by getJSON/sendJSON.
let _authHeaders: Record<string, string> = {};

export function setAuthHeaders(headers: Record<string, string>) {
  _authHeaders = headers;
}

// ── Fetch helpers ─────────────────────────────────────────────────────

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
  const response = await fetch(path, { headers: _authHeaders });
  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response));
  }
  return response.json() as Promise<T>;
}

async function sendJSON<T>(method: "POST" | "PATCH", path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", ..._authHeaders },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response));
  }
  return response.json() as Promise<T>;
}

export interface PaginatedResponse<T> {
  items: T[];
  next_cursor: number | null;
}

export function listSamples(
  params?: { status?: string; client_id?: number; cursor?: number },
): Promise<PaginatedResponse<SampleListItem>> {
  const queryParts: string[] = [];
  if (params?.status) queryParts.push(`status=${encodeURIComponent(params.status)}`);
  if (params?.client_id !== undefined) queryParts.push(`client_id=${params.client_id}`);
  if (params?.cursor !== undefined) queryParts.push(`cursor=${params.cursor}`);
  const query = queryParts.length > 0 ? `?${queryParts.join("&")}` : "";
  return getJSON<PaginatedResponse<SampleListItem>>(`/api/samples${query}`);
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

// ---------------------------------------------------------------------------
// Drill Holes
// ---------------------------------------------------------------------------

export interface DrillHole {
  id: number;
  project_id: number;
  hole_id: string;
  easting: string | null;
  northing: string | null;
  elevation_m: string | null;
  utm_zone: string | null;
  total_depth_m: string | null;
  dip_degrees: string | null;
  azimuth_degrees: string | null;
  drilling_method: string | null;
}

export function listDrillHoles(params?: {
  project_id?: number;
  limit?: number;
}): Promise<DrillHole[]> {
  const q: string[] = [];
  if (params?.project_id !== undefined) q.push(`project_id=${params.project_id}`);
  if (params?.limit !== undefined) q.push(`limit=${params.limit}`);
  const query = q.length > 0 ? `?${q.join("&")}` : "";
  return getJSON<DrillHole[]>(`/api/drill-holes${query}`);
}

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------

export interface Project {
  id: number;
  client_id: number;
  name: string;
  description: string | null;
  location: string | null;
  start_date: string | null;
  end_date: string | null;
}

export function listProjects(params?: {
  client_id?: number;
  limit?: number;
}): Promise<Project[]> {
  const q: string[] = [];
  if (params?.client_id !== undefined) q.push(`client_id=${params.client_id}`);
  if (params?.limit !== undefined) q.push(`limit=${params.limit}`);
  const query = q.length > 0 ? `?${q.join("&")}` : "";
  return getJSON<Project[]>(`/api/projects${query}`);
}

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------

export interface AuditEvent {
  id: number;
  table_name: string;
  record_id: number;
  action: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  reason: string | null;
  actor_id: number | null;
  created_at: string | null;
}

export function getAuditEvents(params?: {
  table_name?: string;
  record_id?: number;
  limit?: number;
}): Promise<AuditEvent[]> {
  const q: string[] = [];
  if (params?.table_name) q.push(`table_name=${encodeURIComponent(params.table_name)}`);
  if (params?.record_id !== undefined) q.push(`record_id=${params.record_id}`);
  if (params?.limit !== undefined) q.push(`limit=${params.limit}`);
  const query = q.length > 0 ? `?${q.join("&")}` : "";
  return getJSON<AuditEvent[]>(`/api/audit/events${query}`);
}

export interface ClientListItem {
  id: number;
  code: string;
  name: string;
  is_active: boolean;
  submission_count: number;
}

export interface Client {
  id: number;
  code: string;
  name: string;
  contact_person: string | null;
  email: string | null;
  phone: string | null;
  billing_address: string | null;
  is_active: boolean;
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

// ---------------------------------------------------------------------------
// Submissions
// ---------------------------------------------------------------------------

export function listSubmissions(params?: {
  client_id?: number;
  limit?: number;
  cursor?: number;
}): Promise<PaginatedResponse<SubmissionListItem>> {
  const q: string[] = [];
  if (params?.client_id !== undefined) q.push(`client_id=${params.client_id}`);
  if (params?.limit !== undefined) q.push(`limit=${params.limit}`);
  if (params?.cursor !== undefined) q.push(`cursor=${params.cursor}`);
  const query = q.length > 0 ? `?${q.join("&")}` : "";
  return getJSON<PaginatedResponse<SubmissionListItem>>(`/api/submissions${query}`);
}

export function getSubmission(id: number): Promise<Submission> {
  return getJSON<Submission>(`/api/submissions/${id}`);
}

// ---------------------------------------------------------------------------
// Certificates
// ---------------------------------------------------------------------------

export function listCertificates(params?: {
  client_id?: number;
  limit?: number;
  cursor?: number;
}): Promise<PaginatedResponse<CertificateListItem>> {
  const q: string[] = [];
  if (params?.client_id !== undefined) q.push(`client_id=${params.client_id}`);
  if (params?.limit !== undefined) q.push(`limit=${params.limit}`);
  if (params?.cursor !== undefined) q.push(`cursor=${params.cursor}`);
  const query = q.length > 0 ? `?${q.join("&")}` : "";
  return getJSON<PaginatedResponse<CertificateListItem>>(`/api/certificates${query}`);
}

// ---------------------------------------------------------------------------
// Instruments
// ---------------------------------------------------------------------------

export function listInstruments(params?: {
  instrument_type?: string;
  status?: string;
}): Promise<Instrument[]> {
  const q: string[] = [];
  if (params?.instrument_type) q.push(`instrument_type=${encodeURIComponent(params.instrument_type)}`);
  if (params?.status) q.push(`status=${encodeURIComponent(params.status)}`);
  const query = q.length > 0 ? `?${q.join("&")}` : "";
  return getJSON<Instrument[]>(`/api/instruments${query}`);
}

export function createInstrument(body: {
  name: string;
  instrument_type: string;
  manufacturer?: string;
  model?: string;
  serial_number?: string;
  location?: string;
  calibration_due_on?: string;
  balance_sensitivity_mg?: string;
  solution_detection_limit?: string;
}): Promise<Instrument> {
  return sendJSON<Instrument>("POST", "/api/instruments", body);
}

export function updateInstrument(
  id: number,
  body: {
    name?: string;
    manufacturer?: string;
    model?: string;
    serial_number?: string;
    location?: string;
    status?: string;
    calibration_due_on?: string;
    balance_sensitivity_mg?: string;
    solution_detection_limit?: string;
    reason?: string;
  },
): Promise<Instrument> {
  return sendJSON<Instrument>("PATCH", `/api/instruments/${id}`, body);
}

// ---------------------------------------------------------------------------
// Sentinel
// ---------------------------------------------------------------------------

export interface SentinelSubmitResponse {
  submission_id: number;
  http_status: number | null;
  sentinel_import_id: string | null;
  error: string | null;
}

export interface SentinelVerdictResponse {
  status: "never_submitted" | "pending" | "verdicted";
  verdict: Record<string, unknown> | null;
  submitted_at: string | null;
  last_polled_at: string | null;
  http_status?: number | null;
}

export function submitToSentinel(batchId: number): Promise<SentinelSubmitResponse> {
  return sendJSON<SentinelSubmitResponse>(
    "POST",
    `/api/batches/${batchId}/submit-to-sentinel`,
    {},
  );
}

export function getSentinelVerdict(batchId: number): Promise<SentinelVerdictResponse> {
  return getJSON<SentinelVerdictResponse>(`/api/batches/${batchId}/sentinel-verdict`);
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

export interface DashboardStats {
  total_samples: number;
  total_clients: number;
  total_submissions: number;
  total_batches: number;
  total_certificates: number;
  samples_by_status: Record<string, number>;
}

export function getDashboardStats(): Promise<DashboardStats> {
  return getJSON<DashboardStats>("/api/stats");
}

// ---------------------------------------------------------------------------
// Clients
// ---------------------------------------------------------------------------

export function getClient(clientId: number): Promise<Client> {
  return getJSON<Client>(`/api/clients/${clientId}`);
}

export function createClient(body: { name: string }): Promise<Client> {
  return sendJSON<Client>("POST", "/api/clients", body);
}

export function updateClientStatus(
  clientId: number,
  body: { is_active: boolean; reason?: string },
): Promise<{ status: string }> {
  return sendJSON<{ status: string }>("PATCH", `/api/clients/${clientId}`, body);
}

// ---------------------------------------------------------------------------
// Prep Records
// ---------------------------------------------------------------------------

export interface PrepRecord {
  id: number;
  sample_id: number;
  stage: string;
  instrument_id: number | null;
  performed_at: string;
  input_weight_g: string | null;
  output_weight_g: string | null;
  supersedes_id: number | null;
  superseded_reason: string | null;
  notes: string | null;
  created_at: string;
}

export function listSamplePrepRecords(sampleId: number): Promise<PrepRecord[]> {
  return getJSON<PrepRecord[]>(`/api/samples/${sampleId}/prep-records`);
}

export function createPrepRecord(body: {
  sample_id: number;
  stage: string;
  instrument_id?: number;
  performed_at: string;
  input_weight_g?: string;
  output_weight_g?: string;
  notes?: string;
}): Promise<PrepRecord> {
  return sendJSON<PrepRecord>("POST", "/api/prep-records", body);
}

// ---------------------------------------------------------------------------
// Fire Assay Results
// ---------------------------------------------------------------------------

export function createFireAssayResult(body: {
  sample_id: number;
  gold_bead_mg: string;
  sample_weight_g: string;
  balance_sensitivity_mg?: string;
  dore_bead_mg?: string;
  analysed_at: string;
  notes?: string;
  crucible_id?: number;
  instrument_id?: number;
}): Promise<{ id: number }> {
  return sendJSON<{ id: number }>("POST", "/api/fire-assay-results", body);
}

export function createSolutionFinish(body: {
  sample_id: number;
  method: string;
  concentration: string;
  concentration_unit: string;
  solution_volume_ml?: string;
  sample_weight_g?: string;
  analysed_at: string;
  detection_limit?: string;
  upper_calibration_limit?: string;
  notes?: string;
  crucible_id?: number;
  instrument_id?: number;
}): Promise<{ id: number }> {
  return sendJSON<{ id: number }>(
    "POST",
    "/api/fire-assay-results/solution-finish",
    body,
  );
}

export { ApiError };
