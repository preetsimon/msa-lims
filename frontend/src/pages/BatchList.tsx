import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { createBatch, listBatches, listInstruments } from "../api";
import { PageHeader } from "../components/PageHeader";
import { StatusPill } from "../components/StatusPill";
import { datetimeLocalValueToIso, nowAsDatetimeLocalValue } from "../datetimeInput";
import type { Batch } from "../types";

export function BatchList() {
  const navigate = useNavigate();
  const [batches, setBatches] = useState<Batch[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // ── Open-batch form ─────────────────────────────────────────────────
  const [furnaces, setFurnaces] = useState<{ id: number; name: string }[]>([]);
  const [openedAt, setOpenedAt] = useState(nowAsDatetimeLocalValue());
  const [instrumentId, setInstrumentId] = useState<number | "">("");
  const [notes, setNotes] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listBatches()
      .then((data) => {
        if (!cancelled) setBatches(data);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load batches.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    listInstruments({ instrument_type: "furnace" })
      .then((list) => setFurnaces(list.map((i) => ({ id: i.id, name: i.name }))))
      .catch(() => {});
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setFormError(null);
    try {
      const created = await createBatch({
        opened_at: datetimeLocalValueToIso(openedAt),
        ...(instrumentId !== "" ? { instrument_id: instrumentId } : {}),
        ...(notes.trim() ? { notes: notes.trim() } : {}),
      });
      navigate(`/batches/${created.id}`);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Could not open the batch.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main>
      <PageHeader title="Batches" lede="Every furnace run, most recent first." />

      <form className="card form-grid" onSubmit={handleCreate}>
        <h2>Open a batch</h2>
        <div className="form-row">
          <label>
            Opened
            <input
              type="datetime-local"
              value={openedAt}
              onChange={(e) => setOpenedAt(e.target.value)}
              required
            />
          </label>
          <label>
            Furnace (optional)
            <select
              value={instrumentId}
              onChange={(e) =>
                setInstrumentId(e.target.value === "" ? "" : Number(e.target.value))
              }
            >
              <option value="">No furnace recorded</option>
              {furnaces.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label>
          Notes (optional)
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} />
        </label>
        {formError && <p className="page-error">{formError}</p>}
        <div className="form-actions">
          <button type="submit" className="btn-primary" disabled={submitting}>
            {submitting ? "Opening…" : "Open batch"}
          </button>
        </div>
      </form>

      {error && <p className="page-error">{error}</p>}
      {!batches && !error && <p className="muted">Loading…</p>}
      {batches && batches.length === 0 && <p className="empty-state">No batches opened yet.</p>}

      {batches && batches.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Batch</th>
                <th>Status</th>
                <th>Opened</th>
              </tr>
            </thead>
            <tbody>
              {batches.map((batch) => (
                <tr key={batch.id}>
                  <td>
                    <Link to={`/batches/${batch.id}`}>{batch.batch_number}</Link>
                  </td>
                  <td>
                    <StatusPill status={batch.status} />
                  </td>
                  <td className="muted">{batch.opened_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
