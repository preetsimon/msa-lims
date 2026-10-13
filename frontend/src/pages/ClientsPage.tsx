import { useEffect, useState } from "react";

import {
  createClient,
  listClients,
  updateClientStatus,
  type ClientListItem,
} from "../api";
import { PageHeader } from "../components/PageHeader";

export function ClientsPage() {
  const [clients, setClients] = useState<ClientListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [newCode, setNewCode] = useState("");
  const [newName, setNewName] = useState("");
  const [editing, setEditing] = useState<ClientListItem | null>(null);
  const [editActive, setEditActive] = useState(true);
  const [loading, setLoading] = useState(false);

  const load = () => listClients().then(setClients).catch((e) => setError(String(e)));

  useEffect(() => {
    load();
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newCode.trim() || !newName.trim()) return;
    setLoading(true);
    setError(null);
    try {
      await createClient({ code: newCode.trim().toUpperCase(), name: newName.trim() });
      setNewCode("");
      setNewName("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the client.");
    } finally {
      setLoading(false);
    }
  };

  const handleStatusUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editing) return;
    setLoading(true);
    setError(null);
    try {
      await updateClientStatus(editing.id, { is_active: editActive });
      setEditing(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update the client.");
    } finally {
      setLoading(false);
    }
  };

  const openEdit = (c: ClientListItem) => {
    setEditing(c);
    setEditActive(c.is_active);
  };

  return (
    <main>
      <PageHeader title="Clients" lede="The mining companies whose samples the lab assays." />

      {error && <p className="page-error">{error}</p>}

      <form className="card form-grid" onSubmit={handleCreate}>
        <h2>Add a client</h2>
        <label>
          Client code
          <input
            value={newCode}
            onChange={(e) => setNewCode(e.target.value)}
            placeholder="e.g. MSA"
            maxLength={12}
            required
          />
          <span className="field-hint">A short uppercase code, up to 12 characters.</span>
        </label>
        <label>
          Client name
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="MSA Test Mining Co"
            required
          />
        </label>
        <div className="form-actions">
          <button
            type="submit"
            className="btn-primary"
            disabled={loading || !newCode.trim() || !newName.trim()}
          >
            {loading ? "Adding…" : "Add client"}
          </button>
        </div>
      </form>

      {editing && (
        <form className="card form-grid" onSubmit={handleStatusUpdate}>
          <h2>Edit client — {editing.name}</h2>
          <label>
            Status
            <select
              value={editActive ? "true" : "false"}
              onChange={(e) => setEditActive(e.target.value === "true")}
            >
              <option value="true">Active</option>
              <option value="false">Inactive (deactivate)</option>
            </select>
          </label>
          <div className="form-actions">
            <button type="submit" className="btn-primary" disabled={loading}>
              {loading ? "Saving…" : "Save"}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setEditing(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {clients.length === 0 && !error && <p className="empty-state">No clients yet.</p>}

      {clients.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Code</th>
                <th>Name</th>
                <th>Status</th>
                <th>Submissions</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {clients.map((c) => (
                <tr key={c.id} className={c.is_active ? undefined : "row-inactive"}>
                  <td>{c.code}</td>
                  <td>{c.name}</td>
                  <td>
                    <span className={`pill pill-${c.is_active ? "ok" : "neutral"}`}>
                      {c.is_active ? "active" : "inactive"}
                    </span>
                  </td>
                  <td className="muted">{c.submission_count}</td>
                  <td>
                    <button type="button" className="btn-sm" onClick={() => openEdit(c)}>
                      Edit
                    </button>
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
