import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, getBatch } from "../api";
import { FurnaceTray } from "../components/FurnaceTray";
import { StatusPill } from "../components/StatusPill";
import type { BatchDetail as BatchDetailData } from "../types";

export function BatchDetail() {
  const { id } = useParams<{ id: string }>();
  const [batch, setBatch] = useState<BatchDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);

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
            />
          </section>

          {batch.notes && (
            <section>
              <h2>Notes</h2>
              <p>{batch.notes}</p>
            </section>
          )}
        </>
      )}
    </main>
  );
}
