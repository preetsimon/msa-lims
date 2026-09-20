import { useEffect, useState } from "react";

import {
  createClient,
  listClients,
  updateClientStatus,
  type ClientListItem,
} from "../api";

export function ClientsPage() {
  const [clients, setClients] = useState<ClientListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [editing, setEditing] = useState<ClientListItem | null>(null);
  const [editActive, setEditActive] = useState(true);
  const [editReason, setEditReason] = useState("");
  const [loading, setLoading] = useState(false);

  const load = () => listClients().then(setClients).catch((e) => setError(String(e)));

  useEffect(() => {
    load();
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim()) return;
    setLoading(true);
    try {
      await createClient({ name: newName.trim() });
      setNewName("");
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const handleStatusUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editing) return;
    setLoading(true);
    try {
      await updateClientStatus(editing.id, {
        is_active: editActive,
        ...(editReason ? { reason: editReason } : {}),
      });
      setEditing(null);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: 24 }}>
      <h1>Clients</h1>
      {error && <div className="page-error">{error}</div>}

      <form onSubmit={handleCreate} style={{ marginBottom: 24, display: "flex", gap: 8 }}>
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder="Client name"
          style={{ flex: 1, padding: 8 }}
        />
        <button type="submit" disabled={loading || !newName.trim()}>
          Add Client
        </button>
      </form>

      {editing && (
        <div style={{ border: "1px solid #3b82f6", borderRadius: 8, padding: 16, marginBottom: 24 }}>
          <h3>Edit Client: {editing.name}</h3>
          <form onSubmit={handleStatusUpdate} style={{ display: "flex", flexDirection: "column", gap: 8, maxWidth: 400 }}>
            <label>
              Active
              <select
                value={editActive ? "true" : "false"}
                onChange={(e) => setEditActive(e.target.value === "true")}
                style={{ marginLeft: 8 }}
              >
                <option value="true">Yes</option>
                <option value="false">No (deactivate)</option>
              </select>
            </label>
            {!editActive && (
              <input
                value={editReason}
                onChange={(e) => setEditReason(e.target.value)}
                placeholder="Reason for deactivation"
              />
            )}
            <div style={{ display: "flex", gap: 8 }}>
              <button type="submit" disabled={loading}>
                Save
              </button>
              <button type="button" onClick={() => setEditing(null)}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ borderBottom: "2px solid #ddd" }}>
            <th style={{ textAlign: "left", padding: 8 }}>ID</th>
            <th style={{ textAlign: "left", padding: 8 }}>Code</th>
            <th style={{ textAlign: "left", padding: 8 }}>Name</th>
            <th style={{ textAlign: "left", padding: 8 }}>Active</th>
            <th style={{ textAlign: "left", padding: 8 }}>Submissions</th>
            <th style={{ padding: 8 }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {clients.map((c) => (
            <tr key={c.id} style={{ borderBottom: "1px solid #eee" }}>
              <td style={{ padding: 8 }}>{c.id}</td>
              <td style={{ padding: 8 }}>{c.code}</td>
              <td style={{ padding: 8 }}>{c.name}</td>
              <td style={{ padding: 8 }}>{c.is_active ? "Yes" : "No"}</td>
              <td style={{ padding: 8 }}>{c.submission_count}</td>
              <td style={{ padding: 8, textAlign: "center" }}>
                <button
                  onClick={() => {
                    setEditing(c);
                    setEditActive(true);
                    setEditReason("");
                  }}
                  style={{ fontSize: 12 }}
                >
                  Edit
                </button>
              </td>
            </tr>
          ))}
          {clients.length === 0 && (
            <tr>
              <td colSpan={5} style={{ padding: 16, textAlign: "center", color: "#888" }}>
                No clients yet
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
