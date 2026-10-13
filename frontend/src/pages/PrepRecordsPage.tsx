import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getSample, listSamplePrepRecords, type PrepRecord } from "../api";
import { PageHeader } from "../components/PageHeader";

export function PrepRecordsPage() {
  const { id } = useParams<{ id: string }>();
  const sampleId = Number(id);
  const [records, setRecords] = useState<PrepRecord[]>([]);
  const [sampleInfo, setSampleInfo] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      getSample(sampleId).then((s) => setSampleInfo(s.sample_id)),
      listSamplePrepRecords(sampleId).then(setRecords),
    ])
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [sampleId]);

  return (
    <main>
      <p>
        <Link to={`/samples/${sampleId}`}>&larr; Back to sample</Link>
      </p>

      {loading && <p className="muted">Loading…</p>}
      {error && <p className="page-error">{error}</p>}

      {!loading && !error && (
        <>
          <PageHeader
            title={`Prep Records — ${sampleInfo}`}
            lede="What physically happened to this sample before it was charged."
          />

          {records.length === 0 && <p className="empty-state">No prep records yet.</p>}

          {records.length > 0 && (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Stage</th>
                    <th>Performed At</th>
                    <th>Input (g)</th>
                    <th>Output (g)</th>
                    <th>Supersedes</th>
                    <th>Notes</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {records.map((r) => (
                    <tr key={r.id}>
                      <td className="muted">{r.id}</td>
                      <td>{r.stage}</td>
                      <td>{r.performed_at}</td>
                      <td>{r.input_weight_g ?? "—"}</td>
                      <td>{r.output_weight_g ?? "—"}</td>
                      <td className="muted">{r.supersedes_id ?? "—"}</td>
                      <td className="muted">{r.notes ?? "—"}</td>
                      <td className="muted">{new Date(r.created_at).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </main>
  );
}
