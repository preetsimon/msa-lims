import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ApiError,
  importMultiElementResults,
  listMultiElementResults,
  type MultiElementResultOut,
} from "../api";

const DIGEST_METHODS = ["aqua_regia", "four_acid", "peroxide_fusion"] as const;

/** Common ICP-MS elements grouped by geochemical family. */
const ELEMENT_GROUPS: Record<string, string[]> = {
  "Precious Metals": ["Au", "Ag", "Pd", "Pt"],
  "Base Metals": ["Cu", "Zn", "Pb", "Ni", "Co", "Sn"],
  "Iron Group": ["Fe", "Mn", "Cr", "V", "Ti"],
  "Lithophile": ["Al", "Ca", "Mg", "Na", "K", "Ba", "Sr", "La", "Li"],
  "Chalcophile": ["As", "Sb", "Bi", "Se", "Te", "Mo", "W", "Re"],
  "Volatiles": ["Hg", "Tl", "Cd", "B", "Ge", "In"],
  "Rare Earths": ["Y", "Yb", "Sc", "Nb", "Zr", "Ta", "Th", "U"],
  "Other": ["P", "S", "Ga", "Be"],
};

interface ParsedRow {
  element: string;
  grade_value: string;
  grade_unit: string;
  detection_limit: string;
}

function parseTsv(text: string): ParsedRow[] {
  const lines = text.trim().split("\n");
  if (lines.length < 2) return [];

  const firstLine = lines[0];
  if (!firstLine) return [];
  const header = firstLine.toLowerCase().split("\t");
  const elementCol = header.findIndex((h) => h === "element" || h === "analyte");
  const valueCol = header.findIndex(
    (h) => h === "grade" || h === "result" || h === "value" || h === "concentration",
  );
  const unitCol = header.findIndex(
    (h) => h === "unit" || h === "units" || h === "grade_unit",
  );
  const dlCol = header.findIndex(
    (h) => h === "detection_limit" || h === "dl" || h === "lod" || h === "loq",
  );

  if (elementCol === -1 || valueCol === -1) return [];

  return lines
    .slice(1)
    .map((line) => {
      const cols = line.split("\t");
      const element = (cols[elementCol] ?? "").trim();
      const grade_value = (cols[valueCol] ?? "").trim();
      const grade_unit = unitCol >= 0 ? (cols[unitCol] ?? "ppm").trim() : "ppm";
      const detection_limit = dlCol >= 0 ? (cols[dlCol] ?? "").trim() : "";
      return { element, grade_value, grade_unit, detection_limit };
    })
    .filter((r) => r.element && r.grade_value);
}

export function MultiElementImport() {
  const { id } = useParams<{ id: string }>();
  const sampleId = Number(id);

  const [digestMethod, setDigestMethod] = useState<string>("aqua_regia");
  const [methodNotes, setMethodNotes] = useState("");
  const [analysedAt, setAnalysedAt] = useState(() => new Date().toISOString().slice(0, 16));
  const [pasteText, setPasteText] = useState("");
  const [parsed, setParsed] = useState<ParsedRow[]>([]);
  const [existing, setExisting] = useState<MultiElementResultOut[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<number | null>(null);

  useEffect(() => {
    listMultiElementResults(sampleId)
      .then(setExisting)
      .catch(() => {});
  }, [sampleId]);

  const handleParse = useCallback(() => {
    const rows = parseTsv(pasteText);
    setParsed(rows);
    if (rows.length === 0) {
      setError("No valid rows found. Expected TSV with 'element' and 'result' columns.");
    } else {
      setError(null);
    }
  }, [pasteText]);

  const handleSubmit = async () => {
    if (parsed.length === 0) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await importMultiElementResults(sampleId, {
        sample_id: sampleId,
        digest_method: digestMethod,
        method_notes: methodNotes || null,
        analysed_at: new Date(analysedAt).toISOString(),
        results: parsed.map((r) => ({
          element: r.element,
          grade_value: r.grade_value,
          grade_unit: r.grade_unit,
          detection_limit: r.detection_limit || null,
        })),
      });
      setSuccess(response.imported.length);
      setParsed([]);
      setPasteText("");
      const updated = await listMultiElementResults(sampleId);
      setExisting(updated);
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.message : "Import failed.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main>
      <p>
        <Link to={`/samples/${sampleId}`}>&larr; Sample detail</Link>
      </p>

      <h1>Import Multi-Element Results</h1>
      <p className="lede">Sample #{sampleId}</p>

      {error && <p className="error">{error}</p>}
      {success !== null && (
        <p className="ok">
          Imported {success} element{success !== 1 ? "s" : ""}.
        </p>
      )}

      <section>
        <h2>Settings</h2>
        <div className="form-field">
          <label htmlFor="digest">Digest method</label>
          <select id="digest" value={digestMethod} onChange={(e) => setDigestMethod(e.target.value)}>
            {DIGEST_METHODS.map((m) => (
              <option key={m} value={m}>
                {m.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
        <div className="form-field">
          <label htmlFor="analysed">Analysed at</label>
          <input
            id="analysed"
            type="datetime-local"
            value={analysedAt}
            onChange={(e) => setAnalysedAt(e.target.value)}
          />
        </div>
        <div className="form-field">
          <label htmlFor="notes">Method notes (optional)</label>
          <textarea
            id="notes"
            rows={2}
            value={methodNotes}
            onChange={(e) => setMethodNotes(e.target.value)}
          />
        </div>
      </section>

      <section>
        <h2>Paste instrument export</h2>
        <p className="muted">
          Paste a tab-separated export with columns: element, result, unit, detection_limit.
        </p>
        <div className="form-field">
          <textarea
            rows={10}
            placeholder={"Au\t5.000\tppm\t0.01\nCu\t120.5\tppm\t0.5\nZn\t85.2\tppm\t1.0"}
            value={pasteText}
            onChange={(e) => setPasteText(e.target.value)}
          />
        </div>
        <div className="form-actions">
          <button className="btn-secondary" onClick={handleParse} disabled={!pasteText.trim()}>
            Parse
          </button>
          <button
            className="btn-primary"
            onClick={handleSubmit}
            disabled={submitting || parsed.length === 0}
          >
            {submitting ? "Importing…" : `Import ${parsed.length} elements`}
          </button>
        </div>
      </section>

      {parsed.length > 0 && (
        <section>
          <h2>Preview ({parsed.length} elements)</h2>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Element</th>
                  <th>Grade</th>
                  <th>Unit</th>
                  <th>Detection Limit</th>
                </tr>
              </thead>
              <tbody>
                {parsed.map((row, i) => (
                  <tr key={`${row.element}-${i}`}>
                    <td>{row.element}</td>
                    <td>{row.grade_value}</td>
                    <td>{row.grade_unit}</td>
                    <td className="muted">{row.detection_limit || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {existing.length > 0 && (
        <section>
          <h2>Current results ({existing.length} elements)</h2>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Element</th>
                  <th>Grade</th>
                  <th>Unit</th>
                  <th>Digest</th>
                  <th>Analysed</th>
                </tr>
              </thead>
              <tbody>
                {existing.map((row) => (
                  <tr key={row.id}>
                    <td>{row.element}</td>
                    <td>{row.grade_value}</td>
                    <td>{row.grade_unit}</td>
                    <td className="muted">{row.digest_method.replace(/_/g, " ")}</td>
                    <td className="muted">{row.analysed_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section>
        <h2>Expected elements</h2>
        <p className="muted">Common ICP-MS suite by geochemical family.</p>
        {Object.entries(ELEMENT_GROUPS).map(([group, elements]) => (
          <details key={group}>
            <summary>
              {group} ({elements.length})
            </summary>
            <p className="muted" style={{ margin: "0.25rem 0 0.5rem" }}>
              {elements.join(", ")}
            </p>
          </details>
        ))}
      </section>
    </main>
  );
}
