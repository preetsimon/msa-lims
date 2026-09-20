import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { listClients, listSubmissions, type ClientListItem } from "../api";
import type { SubmissionListItem } from "../types";

export function SubmissionsPage() {
  const [submissions, setSubmissions] = useState<SubmissionListItem[] | null>(null);
  const [clients, setClients] = useState<ClientListItem[]>([]);
  const [clientId, setClientId] = useState<number | "">("");
  const [cursor, setCursor] = useState<number | undefined>(undefined);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listClients()
      .then(setClients)
      .catch(() => {});
  }, []);

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

  const handleNext = () => {
    if (nextCursor !== null) setCursor(nextCursor);
  };

  const handlePrev = () => {
    setCursor(undefined);
    setSubmissions(null);
  };

  return (
    <main>
      <header>
        <h1>Submissions</h1>
        <p className="lede">Work orders received from clients.</p>
      </header>

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

      {error && <p className="error">{error}</p>}
      {!submissions && !error && <p className="muted">Loading…</p>}
      {submissions && submissions.length === 0 && (
        <p className="muted">No submissions yet.</p>
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
