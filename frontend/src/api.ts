/**
 * Typed fetch helpers over the API the Vite dev server proxies to :8002.
 *
 * No auth headers are sent — `MSA_AUTH_MODE=dev_headers` (this stack's local
 * default) resolves an unheadered request to the least-privileged role, the
 * same way every curl check in PROGRESS.md's live verifications does.
 */

import type { Batch, BatchDetail, SampleDetail, SampleListItem } from "./types";

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function getJSON<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    const body = await response.text();
    throw new ApiError(response.status, body || response.statusText);
  }
  return response.json() as Promise<T>;
}

export function listSamples(): Promise<SampleListItem[]> {
  return getJSON<SampleListItem[]>("/api/samples");
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

export { ApiError };
