import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, advanceBatchStatus, getBatch, getSentinelVerdict, submitToSentinel } from "../api";
import type { SentinelVerdictResponse } from "../api";
import { ChargeCrucibleModal } from "../components/ChargeCrucibleModal";
import { FurnaceTray } from "../components/FurnaceTray";
import { PartCrucibleModal } from "../components/PartCrucibleModal";
import { StatusPill } from "../components/StatusPill";
import { WeighCrucibleModal } from "../components/WeighCrucibleModal";
import type { BatchDetail as BatchDetailData, CrucibleSlot } from "../types";

/** The batch lifecycle is strictly linear — see `domain/batch_lifecycle.py` —
 * so "the next status" is a lookup, not a choice. */
const BATCH_STATUS_ORDER = [
  "pending",
  "charging",
  "in_fusion",
  "fused",
  "in_cupellation",
  "cupelled",
  "completed",
] as const;

const ADVANCE_LABEL: Record<string, string> = {
  charging: "Open for charging",
  in_fusion: "Close charging & load furnace",
  fused: "Record fusion complete",
  in_cupellation: "Begin cupellation",
  cupelled: "Record cupellation complete",
  completed: "Close the batch",
};

function nextStatus(current: string): string | null {
  const index = BATCH_STATUS_ORDER.indexOf(current as (typeof BATCH_STATUS_ORDER)[number]);
  if (index === -1 || index === BATCH_STATUS_ORDER.length - 1) return null;
  return BATCH_STATUS_ORDER[index + 1] ?? null;
}

type ModalState =
  | { kind: "charge"; row: number; col: number }
  | { kind: "part"; slot: CrucibleSlot }
  | { kind: "weigh"; slot: CrucibleSlot }
  | null;

export function BatchDetail() {
  const { id } = useParams<{ id: string }>();
  const [batch, setBatch] = useState<BatchDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [modal, setModal] = useState<ModalState>(null);
  const [advancing, setAdvancing] = useState(false);
  const [advanceError, setAdvanceError] = useState<string | null>(null);
  const [verdict, setVerdict] = useState<SentinelVerdictResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const refetch = useCallback(() => {
    if (!id) return;
    getBatch(Number(id))
      .then(setBatch)
      .catch((err: unknown) => {
        setError(
          err instanceof ApiError && err.status === 404
            ? `No batch with id ${id}.`
            : "Could not load this batch.",
        );
      });
  }, [id]);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setBatch(null);
    setError(null);
    getBatch(Number(id))
      .then((data) => {
        if (!cancelled) setBatch(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError && err.status === 404
            ? `No batch with id ${id}.`
            : "Could not load this batch.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  // Fetch Sentinel verdict when batch is loaded.
  useEffect(() => {
    if (!id || !batch) return;
    let cancelled = false;
    getSentinelVerdict(Number(id))
      .then((data) => {
        if (!cancelled) setVerdict(data);
      })
      .catch(() => {
        // Sentinel verdict fetch is best-effort — don't block the page.
      });
    return () => {
      cancelled = true;
    };
  }, [id, batch]);

  async function handleAdvance() {
    if (!batch) return;
    const target = nextStatus(batch.status);
    if (!target) return;
    setAdvanceError(null);
    setAdvancing(true);
    try {
      await advanceBatchStatus(batch.id, target);
      refetch();
    } catch (err: unknown) {
      setAdvanceError(err instanceof ApiError ? err.message : "Advancing the batch failed.");
    } finally {
      setAdvancing(false);
    }
  }

  async function handleSubmitToSentinel() {
    if (!batch) return;
    setSubmitError(null);
    setSubmitting(true);
    try {
      await submitToSentinel(batch.id);
      // Re-fetch verdict after submission.
      const v = await getSentinelVerdict(batch.id);
      setVerdict(v);
    } catch (err: unknown) {
      setSubmitError(err instanceof ApiError ? err.message : "Submission to Sentinel failed.");
    } finally {
      setSubmitting(false);
    }
  }

  const target = batch ? nextStatus(batch.status) : null;

  return (
    <main>
      <p>
        <Link to="/batches">&larr; All batches</Link>
      </p>

      {error && <p className="error">{error}</p>}
      {!batch && !error && <p className="muted">Loading…</p>}

      {batch && (
        <>
          <header>
            <h1>{batch.batch_number}</h1>
            <p className="lede">
              <StatusPill status={batch.status} /> · opened {batch.opened_at}
            </p>
            {target && (
              <p>
                <button type="button" className="btn-primary" onClick={handleAdvance} disabled={advancing}>
                  {advancing ? "Advancing…" : ADVANCE_LABEL[target]}
                </button>
              </p>
            )}
            {advanceError && <p className="error">{advanceError}</p>}
          </header>

          <section>
            <h2>
              Furnace tray — {batch.furnace_rows}×{batch.furnace_columns}
            </h2>
            {batch.crucibles.length === 0 ? (
              <p className="muted">No crucibles charged yet.</p>
            ) : null}
            <FurnaceTray
              rows={batch.furnace_rows}
              columns={batch.furnace_columns}
              crucibles={batch.crucibles}
              batchStatus={batch.status}
              onChargeSlot={(row, col) => setModal({ kind: "charge", row, col })}
              onPartCrucible={(slot) => setModal({ kind: "part", slot })}
              onWeighCrucible={(slot) => setModal({ kind: "weigh", slot })}
            />
          </section>

          {batch.notes && (
            <section>
              <h2>Notes</h2>
              <p>{batch.notes}</p>
            </section>
          )}

          <section>
            <h2>QC Sentinel</h2>
            {!verdict && <p className="muted">Loading verdict…</p>}
            {verdict && verdict.status === "never_submitted" && (
              <p>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={handleSubmitToSentinel}
                  disabled={submitting}
                >
                  {submitting ? "Submitting…" : "Submit to Sentinel"}
                </button>
              </p>
            )}
            {verdict && verdict.status === "pending" && (
              <p className="muted">
                Submitted — awaiting verdict.
                {verdict.submitted_at && ` (${verdict.submitted_at})`}
              </p>
            )}
            {verdict && verdict.status === "verdicted" && (
              <div>
                <p>
                  <strong>Sentinel's verdict:</strong>{" "}
                  {verdict.verdict?.verdict === "pass" ? (
                    <span style={{ color: "green" }}>No problems reported</span>
                  ) : (
                    <span style={{ color: "red" }}>
                      {String(verdict.verdict?.verdict ?? "unknown")}
                    </span>
                  )}
                </p>
                {verdict.submitted_at && (
                  <p className="muted">Submitted: {verdict.submitted_at}</p>
                )}
                {verdict.last_polled_at && (
                  <p className="muted">Last polled: {verdict.last_polled_at}</p>
                )}
              </div>
            )}
            {submitError && <p className="error">{submitError}</p>}
          </section>
        </>
      )}

      {batch && modal?.kind === "charge" && (
        <ChargeCrucibleModal
          batchId={batch.id}
          positionRow={modal.row}
          positionCol={modal.col}
          onClose={() => setModal(null)}
          onCharged={refetch}
        />
      )}
      {batch && modal?.kind === "part" && (
        <PartCrucibleModal
          batchId={batch.id}
          crucibleId={modal.slot.id}
          label={`${modal.slot.position_row}-${modal.slot.position_col}`}
          onClose={() => setModal(null)}
          onParted={refetch}
        />
      )}
      {batch && modal?.kind === "weigh" && (
        <WeighCrucibleModal
          batchId={batch.id}
          crucibleId={modal.slot.id}
          label={`${modal.slot.position_row}-${modal.slot.position_col}`}
          onClose={() => setModal(null)}
          onWeighed={refetch}
        />
      )}
    </main>
  );
}
