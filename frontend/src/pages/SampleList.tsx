import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { listSamples } from "../api";
import { StatusPill } from "../components/StatusPill";
import type { SampleListItem } from "../types";

export function SampleList() {
  const [samples, setSamples] = useState<SampleListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listSamples()
      .then((data) => {
        if (!cancelled) setSamples(data);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load samples.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main>
      <header>
        <h1>Samples</h1>
        <p className="lede">Every sample received, most recent first.</p>
      </header>

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
