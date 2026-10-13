import { useEffect, useState } from "react";

import { getAuditEvents, type AuditEvent } from "../api";
import { PageHeader } from "../components/PageHeader";

/** Each write action gets a pill tier, matching the status pills'
 * vocabulary: green for creation, blue for corrections, amber for an
 * in-place amend, red for a deactivation. */
const ACTION_PILL: Record<string, string> = {
  create: "pill-create",
  update: "pill-update",
  amend: "pill-amend",
  supersede: "pill-supersede",
  deactivate: "pill-deactivate",
};

export function AuditLogPage() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [tableName, setTableName] = useState("");
  const [recordId, setRecordId] = useState("");

  const load = () => {
    const params: { table_name?: string; record_id?: number } = {};
    if (tableName) params.table_name = tableName;
    if (recordId) params.record_id = Number(recordId);
    getAuditEvents(params)
      .then(setEvents)
      .catch((e) => setError(String(e)));
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <main>
      <PageHeader
        title="Audit Log"
        lede="Every write, in hash-chained order. Filter by table and record."
      />

      {error && <p className="page-error">{error}</p>}

      <div className="toolbar">
        <input
          value={tableName}
          onChange={(e) => setTableName(e.target.value)}
          placeholder="Table name (e.g. sample)"
        />
        <input
          value={recordId}
          onChange={(e) => setRecordId(e.target.value)}
          placeholder="Record ID"
          type="number"
        />
        <button type="button" className="btn-sm" onClick={load}>
          Search
        </button>
      </div>

      {events.length === 0 && !error && <p className="empty-state">No audit events.</p>}

      {events.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Table</th>
                <th>Record</th>
                <th>Action</th>
                <th>Actor</th>
                <th>Reason</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td className="muted">{e.id}</td>
                  <td>{e.table_name}</td>
                  <td>{e.record_id}</td>
                  <td>
                    <span className={`pill ${ACTION_PILL[e.action] ?? "pill-neutral"}`}>
                      {e.action}
                    </span>
                  </td>
                  <td className="muted">{e.actor_id ?? "—"}</td>
                  <td className="muted">{e.reason ?? "—"}</td>
                  <td className="muted">
                    {e.created_at ? new Date(e.created_at).toLocaleString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
