import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  createCertificate,
  listCertificates,
  listClients,
  listSamples,
  type ClientListItem,
} from "../api";
import { PageHeader } from "../components/PageHeader";
import { datetimeLocalValueToIso, nowAsDatetimeLocalValue } from "../datetimeInput";
import type { CertificateListItem, SampleListItem } from "../types";

export function CertificatesPage() {
  const navigate = useNavigate();
  const [certificates, setCertificates] = useState<CertificateListItem[] | null>(null);
  const [clients, setClients] = useState<ClientListItem[]>([]);
  const [clientId, setClientId] = useState<number | "">("");
  const [cursor, setCursor] = useState<number | undefined>(undefined);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  // ── Issue-certificate form ──────────────────────────────────────────
  const [issueClientId, setIssueClientId] = useState<number | "">("");
  const [clientSamples, setClientSamples] = useState<SampleListItem[] | null>(null);
  const [selectedSampleIds, setSelectedSampleIds] = useState<number[]>([]);
  const [issuedAt, setIssuedAt] = useState(nowAsDatetimeLocalValue());
  const [notes, setNotes] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    listClients()
      .then(setClients)
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (issueClientId === "") {
      setClientSamples(null);
      setSelectedSampleIds([]);
      return;
    }
    let cancelled = false;
    setClientSamples(null);
    listSamples({ client_id: issueClientId })
      .then((data) => {
        if (!cancelled) setClientSamples(data.items);
      })
      .catch(() => {
        if (!cancelled) setClientSamples([]);
      });
    return () => {
      cancelled = true;
    };
  }, [issueClientId]);

  const toggleSample = (id: number) => {
    setSelectedSampleIds((ids) =>
      ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id],
    );
  };

  const handleIssue = async (e: React.FormEvent) => {
    e.preventDefault();
    if (issueClientId === "" || selectedSampleIds.length === 0) return;
    setSubmitting(true);
    setFormError(null);
    try {
      const created = await createCertificate({
        client_id: issueClientId,
        sample_ids: selectedSampleIds,
        issued_at: datetimeLocalValueToIso(issuedAt),
        ...(notes.trim() ? { notes: notes.trim() } : {}),
      });
      navigate(`/certificates/${created.id}`);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Could not issue the certificate.");
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    setCertificates(null);
    setError(null);
    const params = {
      ...(clientId !== "" ? { client_id: clientId } : {}),
      ...(cursor !== undefined ? { cursor } : {}),
    };
    listCertificates(params)
      .then((data) => {
        if (!cancelled) {
          setCertificates(data.items);
          setNextCursor(data.next_cursor);
        }
      })
      .catch(() => {
        if (!cancelled) setError("Could not load certificates.");
      });
    return () => {
      cancelled = true;
    };
  }, [clientId, cursor]);

  const handleNext = () => {
    if (nextCursor !== null) setCursor(nextCursor);
  };

  const handlePrev = () => {
    setCursor(undefined);
    setCertificates(null);
  };

  return (
    <main>
      <PageHeader title="Certificates" lede="Issued Certificates of Analysis." />

      <form className="card form-grid" onSubmit={handleIssue}>
        <h2>Issue a certificate</h2>
        <div className="form-row">
          <label>
            Client
            <select
              value={issueClientId}
              onChange={(e) =>
                setIssueClientId(e.target.value === "" ? "" : Number(e.target.value))
              }
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
            Issued
            <input
              type="datetime-local"
              value={issuedAt}
              onChange={(e) => setIssuedAt(e.target.value)}
              required
            />
          </label>
        </div>

        {issueClientId !== "" && (
          <fieldset className="sample-rows">
            <legend>Samples to certify</legend>
            {clientSamples === null && <p className="muted">Loading samples…</p>}
            {clientSamples && clientSamples.length === 0 && (
              <p className="muted">This client has no samples to certify.</p>
            )}
            {clientSamples &&
              clientSamples.map((s) => (
                <label key={s.id} className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={selectedSampleIds.includes(s.id)}
                    onChange={() => toggleSample(s.id)}
                  />
                  {s.sample_id}
                  <span className="field-hint">
                    {s.status} · {s.submission_number}
                  </span>
                </label>
              ))}
          </fieldset>
        )}

        <label>
          Notes (optional)
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} />
        </label>
        {formError && <p className="page-error">{formError}</p>}
        <div className="form-actions">
          <button
            type="submit"
            className="btn-primary"
            disabled={submitting || issueClientId === "" || selectedSampleIds.length === 0}
          >
            {submitting
              ? "Issuing…"
              : `Issue certificate (${selectedSampleIds.length})`}
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
      {!certificates && !error && <p className="muted">Loading…</p>}
      {certificates && certificates.length === 0 && (
        <p className="empty-state">No certificates issued yet.</p>
      )}

      {certificates && certificates.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Certificate #</th>
                <th>Issued</th>
                <th>Supersedes</th>
                <th>Status</th>
                <th>PDF</th>
              </tr>
            </thead>
            <tbody>
              {certificates.map((cert) => (
                <tr key={cert.id}>
                  <td>
                    <Link to={`/certificates/${cert.id}`}>{cert.certificate_number}</Link>
                  </td>
                  <td className="muted">
                    {new Date(cert.issued_at).toLocaleDateString()}
                  </td>
                  <td className="muted">
                    {cert.supersedes_id ? `#${cert.supersedes_id}` : "—"}
                  </td>
                  <td>
                    {cert.is_stale ? (
                      <span className="pill pill-warn">Superseded</span>
                    ) : (
                      <span className="pill pill-ok">Current</span>
                    )}
                  </td>
                  <td>
                    <a
                      href={`/api/certificates/${cert.id}/pdf`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Download
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {certificates && certificates.length > 0 && (
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
