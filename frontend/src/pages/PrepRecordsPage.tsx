import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { getSample, listSamplePrepRecords, type PrepRecord } from "../api";

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

  if (loading) return <div style={{ padding: 24 }}>Loading...</div>;
  if (error) return <div style={{ padding: 24 }} className="page-error">{error}</div>;

  return (
    <div style={{ padding: 24 }}>
      <h1>Prep Records for {sampleInfo}</h1>

      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ borderBottom: "2px solid #ddd" }}>
            <th style={{ textAlign: "left", padding: 8 }}>ID</th>
            <th style={{ textAlign: "left", padding: 8 }}>Stage</th>
            <th style={{ textAlign: "left", padding: 8 }}>Performed At</th>
            <th style={{ textAlign: "left", padding: 8 }}>Input (g)</th>
            <th style={{ textAlign: "left", padding: 8 }}>Output (g)</th>
            <th style={{ textAlign: "left", padding: 8 }}>Supersedes</th>
            <th style={{ textAlign: "left", padding: 8 }}>Notes</th>
            <th style={{ textAlign: "left", padding: 8 }}>Created</th>
          </tr>
        </thead>
        <tbody>
          {records.map((r) => (
            <tr key={r.id} style={{ borderBottom: "1px solid #eee" }}>
              <td style={{ padding: 8 }}>{r.id}</td>
              <td style={{ padding: 8 }}>{r.stage}</td>
              <td style={{ padding: 8 }}>{r.performed_at}</td>
              <td style={{ padding: 8 }}>{r.input_weight_g ?? "—"}</td>
              <td style={{ padding: 8 }}>{r.output_weight_g ?? "—"}</td>
              <td style={{ padding: 8 }}>{r.supersedes_id ?? "—"}</td>
              <td style={{ padding: 8 }}>{r.notes ?? "—"}</td>
              <td style={{ padding: 8 }}>{new Date(r.created_at).toLocaleString()}</td>
            </tr>
          ))}
          {records.length === 0 && (
            <tr>
              <td colSpan={8} style={{ padding: 16, textAlign: "center", color: "#888" }}>
                No prep records
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
