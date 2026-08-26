import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { listBatches } from "../api";
import { StatusPill } from "../components/StatusPill";
import type { Batch } from "../types";

export function BatchList() {
  const [batches, setBatches] = useState<Batch[] | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <main>
      <header>
        <h1>Batches</h1>
        <p className="lede">Every furnace run, most recent first.</p>
      </header>

      {error && <p className="error">{error}</p>}
      {!batches && !error && <p className="muted">Loading…</p>}
      {batches && batches.length === 0 && <p className="muted">No batches opened yet.</p>}

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
