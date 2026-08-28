import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { listClients, listSamples, type ClientListItem } from "../api";
import { StatusPill } from "../components/StatusPill";
import type { SampleListItem } from "../types";

export function SampleList() {
  const [samples, setSamples] = useState<SampleListItem[] | null>(null);
  const [clients, setClients] = useState<ClientListItem[]>([]);
  const [clientId, setClientId] = useState<number | "">("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listClients()
      .then(setClients)
      .catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    setSamples(null);
    setError(null);
    const params = clientId !== "" ? { client_id: clientId } : undefined;
    listSamples(params)
      .then((data) => {
        if (!cancelled) setSamples(data);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load samples.");
      });
    return () => {
      cancelled = true;
    };
  }, [clientId]);

  return (
    <main>
      <header>
        <h1>Samples</h1>
        <p className="lede">Every sample received, most recent first.</p>
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
      {!samples && !error && <p className="muted">Loading…</p>}
      {samples && samples.length === 0 && <p className="muted">No samples received yet.</p>}

      {samples && samples.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Sample</th>
                <th>Type</th>
                <th>Status</th>
                <th>Client</th>
                <th>Submission</th>
              </tr>
            </thead>
            <tbody>
              {samples.map((sample) => (
                <tr key={sample.id}>
                  <td>
                    <Link to={`/samples/${sample.id}`}>{sample.sample_id}</Link>
                  </td>
                  <td className="muted">{sample.sample_type}</td>
                  <td>
                    <StatusPill status={sample.status} />
                  </td>
                  <td>{sample.client_name}</td>
                  <td className="muted">{sample.submission_number}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
