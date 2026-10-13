import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  createSubmission,
  listClients,
  listProjects,
  listSubmissions,
  type ClientListItem,
  type Project,
} from "../api";
import { PageHeader } from "../components/PageHeader";
import { datetimeLocalValueToIso, nowAsDatetimeLocalValue } from "../datetimeInput";
import type { SubmissionListItem } from "../types";

const SAMPLE_TYPES = ["core", "rc_chip", "soil", "stream_sediment", "rock_chip", "pulp"] as const;

interface SampleRow {
  sample_id: string;
  sample_type: string;
  weight_received_g: string;
}

const blankSample = (): SampleRow => ({
  sample_id: "",
  sample_type: "core",
  weight_received_g: "",
});

export function SubmissionsPage() {
  const navigate = useNavigate();
  const [submissions, setSubmissions] = useState<SubmissionListItem[] | null>(null);
  const [clients, setClients] = useState<ClientListItem[]>([]);
  const [clientId, setClientId] = useState<number | "">("");
  const [cursor, setCursor] = useState<number | undefined>(undefined);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  // ── Receive-submission form ─────────────────────────────────────────
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<number | "">("");
  const [receivedAt, setReceivedAt] = useState(nowAsDatetimeLocalValue());
  const [clientReference, setClientReference] = useState("");
  const [purchaseOrder, setPurchaseOrder] = useState("");
  const [rush, setRush] = useState(false);
  const [comments, setComments] = useState("");
  const [samples, setSamples] = useState<SampleRow[]>([blankSample()]);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    listClients()
      .then(setClients)
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (clientId === "") {
      setProjects([]);
      return;
    }
    listProjects({ client_id: clientId })
      .then(setProjects)
      .catch(() => setProjects([]));
  }, [clientId]);

  useEffect(() => {
    let cancelled = false;
    setSubmissions(null);
    setError(null);
    const params = {
      ...(clientId !== "" ? { client_id: clientId } : {}),
      ...(cursor !== undefined ? { cursor } : {}),
    };
    listSubmissions(params)
      .then((data) => {
        if (!cancelled) {
          setSubmissions(data.items);
          setNextCursor(data.next_cursor);
        }
      })
      .catch(() => {
        if (!cancelled) setError("Could not load submissions.");
      });
    return () => {
      cancelled = true;
    };
  }, [clientId, cursor]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (clientId === "" || samples.some((s) => !s.sample_id.trim())) return;
    setSubmitting(true);
    setFormError(null);
    try {
      const created = await createSubmission({
        client_id: clientId,
        received_at: datetimeLocalValueToIso(receivedAt),
        ...(projectId !== "" ? { project_id: projectId } : {}),
        ...(clientReference.trim() ? { client_reference: clientReference.trim() } : {}),
        ...(purchaseOrder.trim() ? { purchase_order: purchaseOrder.trim() } : {}),
        ...(rush ? { rush: true } : {}),
        ...(comments.trim() ? { comments: comments.trim() } : {}),
        samples: samples.map((s) => ({
          sample_id: s.sample_id.trim(),
          sample_type: s.sample_type,
          ...(s.weight_received_g.trim()
            ? { weight_received_g: s.weight_received_g.trim() }
            : {}),
        })),
      });
      navigate(`/submissions/${created.id}`);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Could not receive the submission.");
    } finally {
      setSubmitting(false);
    }
  };

  const updateSample = (index: number, patch: Partial<SampleRow>) => {
    setSamples((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  };

  const handleNext = () => {
    if (nextCursor !== null) setCursor(nextCursor);
  };

  const handlePrev = () => {
    setCursor(undefined);
    setSubmissions(null);
  };

  return (
    <main>
      <PageHeader title="Submissions" lede="Work orders received from clients." />

      <form className="card form-grid" onSubmit={handleCreate}>
        <h2>Receive a submission</h2>
        <div className="form-row">
          <label>
            Client
            <select
              value={clientId}
              onChange={(e) => {
                const val = e.target.value;
                setClientId(val === "" ? "" : Number(val));
                setProjectId("");
              }}
              required
            >
              <option value="">Select a client…</option>
              {clients.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.code} — {c.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Project (optional)
            <select
              value={projectId}
              onChange={(e) => setProjectId(e.target.value === "" ? "" : Number(e.target.value))}
              disabled={clientId === ""}
            >
              <option value="">No project</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="form-row">
          <label>
            Received
            <input
              type="datetime-local"
              value={receivedAt}
              onChange={(e) => setReceivedAt(e.target.value)}
              required
            />
          </label>
          <label>
            Client reference (optional)
            <input
              value={clientReference}
              onChange={(e) => setClientReference(e.target.value)}
              placeholder="e.g. PO-4471"
            />
          </label>
        </div>

        <div className="form-row">
          <label>
            Purchase order (optional)
            <input value={purchaseOrder} onChange={(e) => setPurchaseOrder(e.target.value)} />
          </label>
          <label className="checkbox-label">
            <input type="checkbox" checked={rush} onChange={(e) => setRush(e.target.checked)} />
            Rush turnaround
          </label>
        </div>

        <label>
          Comments (optional)
          <textarea value={comments} onChange={(e) => setComments(e.target.value)} rows={2} />
        </label>

        <fieldset className="sample-rows">
          <legend>Samples</legend>
          {samples.map((row, i) => (
            <div className="sample-row" key={i}>
              <label>
                Sample ID
                <input
                  value={row.sample_id}
                  onChange={(e) => updateSample(i, { sample_id: e.target.value })}
                  placeholder="MSA-24-001-10.00_12.00"
                  required
                />
              </label>
              <label>
                Type
                <select
                  value={row.sample_type}
                  onChange={(e) => updateSample(i, { sample_type: e.target.value })}
                >
                  {SAMPLE_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Weight (g)
                <input
                  value={row.weight_received_g}
                  onChange={(e) => updateSample(i, { weight_received_g: e.target.value })}
                  inputMode="decimal"
                  placeholder="optional"
                />
              </label>
              <button
                type="button"
                className="btn-sm"
                onClick={() => setSamples((rows) => rows.filter((_, j) => j !== i))}
                disabled={samples.length === 1}
              >
                Remove
              </button>
            </div>
          ))}
          <div className="form-actions">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setSamples((rows) => [...rows, blankSample()])}
            >
              Add sample
            </button>
          </div>
        </fieldset>

        {formError && <p className="page-error">{formError}</p>}

        <div className="form-actions">
          <button
            type="submit"
            className="btn-primary"
            disabled={submitting || clientId === "" || samples.some((s) => !s.sample_id.trim())}
          >
            {submitting ? "Receiving…" : "Receive submission"}
          </button>
        </div>
      </form>

      {clients.length > 0 && (
        <div className="filter-bar">
          <label htmlFor="client-filter">Client</label>
          <select
            id="client-filter"
            value={clientId}
            onChange={(e) => {
              const val = e.target.value;
              setClientId(val === "" ? "" : Number(val));
            }}
          >
            <option value="">All clients</option>
            {clients.map((c) => (
              <option key={c.id} value={c.id}>
                {c.code} — {c.name}
              </option>
            ))}
          </select>
        </div>
      )}

      {error && <p className="page-error">{error}</p>}
      {!submissions && !error && <p className="muted">Loading…</p>}
      {submissions && submissions.length === 0 && (
        <p className="empty-state">No submissions yet.</p>
      )}

      {submissions && submissions.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Submission #</th>
                <th>Client</th>
                <th>Received</th>
                <th>Samples</th>
                <th>Declared</th>
              </tr>
            </thead>
            <tbody>
              {submissions.map((sub) => (
                <tr key={sub.id}>
                  <td>
                    <Link to={`/submissions/${sub.id}`}>{sub.submission_number}</Link>
                  </td>
                  <td>{sub.client_name ?? `Client #${sub.client_id}`}</td>
                  <td className="muted">
                    {new Date(sub.received_at).toLocaleDateString()}
                  </td>
                  <td>{sub.sample_count}</td>
                  <td className="muted">{sub.declared_sample_count ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {submissions && submissions.length > 0 && (
        <div className="pagination">
          <button onClick={handlePrev} disabled={cursor === undefined}>
            Previous
          </button>
          <button onClick={handleNext} disabled={nextCursor === null}>
            Next
          </button>
        </div>
      )}
    </main>
  );
}
