import { useEffect, useState } from "react";

import { getAuditEvents, type AuditEvent } from "../api";

export function AuditLogPage() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [tableName, setTableName] = useState("");
  const [recordId, setRecordId] = useState("");

  const load = () => {
    const params: { table_name?: string; record_id?: number } = {};
    if (tableName) params.table_name = tableName;
    if (recordId) params.record_id = Number(recordId);
    getAuditEvents(params).then(setEvents).catch((e) => setError(String(e)));
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div style={{ padding: 24 }}>
      <h1>Audit Log</h1>
      {error && <div className="page-error">{error}</div>}

      <div style={{ marginBottom: 16, display: "flex", gap: 8, alignItems: "center" }}>
        <input
          value={tableName}
          onChange={(e) => setTableName(e.target.value)}
          placeholder="Table name (e.g. sample)"
          style={{ width: 200 }}
        />
        <input
          value={recordId}
          onChange={(e) => setRecordId(e.target.value)}
          placeholder="Record ID"
          style={{ width: 120 }}
          type="number"
        />
        <button onClick={load}>Search</button>
      </div>

      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ borderBottom: "2px solid #ddd" }}>
            <th style={{ textAlign: "left", padding: 8 }}>ID</th>
            <th style={{ textAlign: "left", padding: 8 }}>Table</th>
            <th style={{ textAlign: "left", padding: 8 }}>Record</th>
            <th style={{ textAlign: "left", padding: 8 }}>Action</th>
            <th style={{ textAlign: "left", padding: 8 }}>Actor</th>
            <th style={{ textAlign: "left", padding: 8 }}>Reason</th>
            <th style={{ textAlign: "left", padding: 8 }}>Created</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id} style={{ borderBottom: "1px solid #eee" }}>
              <td style={{ padding: 8 }}>{e.id}</td>
              <td style={{ padding: 8 }}>{e.table_name}</td>
              <td style={{ padding: 8 }}>{e.record_id}</td>
              <td style={{ padding: 8 }}>
                <span style={{ padding: "2px 6px", borderRadius: 4, fontSize: 12, background: actionColor(e.action), color: "#fff" }}>
                  {e.action}
                </span>
              </td>
              <td style={{ padding: 8 }}>{e.actor_id ?? "—"}</td>
              <td style={{ padding: 8 }}>{e.reason ?? "—"}</td>
              <td style={{ padding: 8 }}>{e.created_at ? new Date(e.created_at).toLocaleString() : "—"}</td>
            </tr>
          ))}
          {events.length === 0 && (
            <tr>
              <td colSpan={7} style={{ padding: 16, textAlign: "center", color: "#888" }}>
                No audit events
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function actionColor(action: string): string {
  switch (action) {
    case "create": return "#10b981";
    case "update": return "#3b82f6";
    case "amend": return "#f59e0b";
    case "supersede": return "#8b5cf6";
    case "deactivate": return "#ef4444";
    default: return "#6b7280";
  }
}
