import { useEffect, useState } from "react";

import { createInstrument, listInstruments, updateInstrument } from "../api";
import type { Instrument } from "../api";

const INSTRUMENT_TYPES = [
  { value: "icp_oes", label: "ICP-OES" },
  { value: "icp_ms", label: "ICP-MS" },
  { value: "atomic_absorption", label: "AAS" },
  { value: "xrf", label: "XRF" },
  { value: "microbalance", label: "Microbalance" },
  { value: "crusher", label: "Crusher" },
  { value: "pulverizer", label: "Pulverizer" },
  { value: "furnace", label: "Furnace" },
  { value: "other", label: "Other" },
];

export function InstrumentsPage() {
  const [instruments, setInstruments] = useState<Instrument[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Instrument | null>(null);

  // Form state
  const [name, setName] = useState("");
  const [type, setType] = useState("icp_oes");
  const [manufacturer, setManufacturer] = useState("");
  const [model, setModel] = useState("");
  const [serialNumber, setSerialNumber] = useState("");
  const [location, setLocation] = useState("");
  const [sensitivity, setSensitivity] = useState("");
  const [detectionLimit, setDetectionLimit] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const load = () => {
    listInstruments()
      .then(setInstruments)
      .catch(() => setError("Could not load instruments."));
  };

  useEffect(() => {
    load();
  }, []);

  const resetForm = () => {
    setName("");
    setType("icp_oes");
    setManufacturer("");
    setModel("");
    setSerialNumber("");
    setLocation("");
    setSensitivity("");
    setDetectionLimit("");
    setFormError(null);
    setEditing(null);
    setShowForm(false);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    try {
      if (editing) {
        await updateInstrument(editing.id, {
          ...(name ? { name } : {}),
          ...(manufacturer ? { manufacturer } : {}),
          ...(model ? { model } : {}),
          ...(serialNumber ? { serial_number: serialNumber } : {}),
          ...(location ? { location } : {}),
          ...(sensitivity ? { balance_sensitivity_mg: sensitivity } : {}),
          ...(detectionLimit ? { solution_detection_limit: detectionLimit } : {}),
          reason: "updated via UI",
        });
      } else {
        await createInstrument({
          name,
          instrument_type: type,
          ...(manufacturer ? { manufacturer } : {}),
          ...(model ? { model } : {}),
          ...(serialNumber ? { serial_number: serialNumber } : {}),
          ...(location ? { location } : {}),
          ...(sensitivity ? { balance_sensitivity_mg: sensitivity } : {}),
          ...(detectionLimit ? { solution_detection_limit: detectionLimit } : {}),
        });
      }
      resetForm();
      load();
    } catch (err: unknown) {
      setFormError(err instanceof Error ? err.message : "Save failed.");
    }
  };

  const startEdit = (inst: Instrument) => {
    setEditing(inst);
    setName(inst.name);
    setType(inst.instrument_type);
    setManufacturer(inst.manufacturer ?? "");
    setModel(inst.model ?? "");
    setSerialNumber(inst.serial_number ?? "");
    setLocation(inst.location ?? "");
    setSensitivity(inst.balance_sensitivity_mg ?? "");
    setDetectionLimit(inst.solution_detection_limit ?? "");
    setShowForm(true);
  };

  return (
    <main>
      <header>
        <h1>Instruments</h1>
        <p className="lede">Lab instruments and their calibration status.</p>
      </header>

      {!showForm && (
        <button className="btn-primary" onClick={() => { resetForm(); setShowForm(true); }}>
          Register instrument
        </button>
      )}

      {showForm && (
        <form className="card form-grid" onSubmit={handleSubmit}>
          <h2>{editing ? "Edit instrument" : "New instrument"}</h2>
          {formError && <p className="error">{formError}</p>}

          <label>
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} required />
          </label>

          <label>
            Type
            <select value={type} onChange={(e) => setType(e.target.value)} disabled={!!editing}>
              {INSTRUMENT_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>

          <label>
            Manufacturer
            <input value={manufacturer} onChange={(e) => setManufacturer(e.target.value)} />
          </label>

          <label>
            Model
            <input value={model} onChange={(e) => setModel(e.target.value)} />
          </label>

          <label>
            Serial number
            <input value={serialNumber} onChange={(e) => setSerialNumber(e.target.value)} />
          </label>

          <label>
            Location
            <input value={location} onChange={(e) => setLocation(e.target.value)} />
          </label>

          {(type === "microbalance" || editing?.instrument_type === "microbalance") && (
            <label>
              Balance sensitivity (mg)
              <input
                type="number"
                step="0.001"
                min="0"
                value={sensitivity}
                onChange={(e) => setSensitivity(e.target.value)}
              />
            </label>
          )}

          {(type === "icp_oes" || type === "icp_ms" || type === "atomic_absorption" ||
            editing?.instrument_type === "icp_oes" ||
            editing?.instrument_type === "icp_ms" ||
            editing?.instrument_type === "atomic_absorption") && (
            <label>
              Detection limit
              <input
                type="number"
                step="0.01"
                min="0"
                value={detectionLimit}
                onChange={(e) => setDetectionLimit(e.target.value)}
              />
            </label>
          )}

          <div className="form-actions">
            <button type="submit" className="btn-primary">
              {editing ? "Save changes" : "Register"}
            </button>
            <button type="button" onClick={resetForm}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {error && <p className="error">{error}</p>}
      {!instruments && !error && <p className="muted">Loading…</p>}
      {instruments && instruments.length === 0 && (
        <p className="muted">No instruments registered.</p>
      )}

      {instruments && instruments.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Manufacturer</th>
                <th>Model</th>
                <th>Location</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {instruments.map((inst) => (
                <tr key={inst.id}>
                  <td>{inst.name}</td>
                  <td className="muted">{inst.instrument_type}</td>
                  <td>{inst.manufacturer ?? "—"}</td>
                  <td>{inst.model ?? "—"}</td>
                  <td>{inst.location ?? "—"}</td>
                  <td>
                    <span className={`pill pill-${inst.status === "active" ? "ok" : "warn"}`}>
                      {inst.status}
                    </span>
                  </td>
                  <td>
                    <button className="btn-sm" onClick={() => startEdit(inst)}>
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
