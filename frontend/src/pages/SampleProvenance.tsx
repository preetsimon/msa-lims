import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, getSampleProvenance } from "../api";
import { StatusPill } from "../components/StatusPill";
import type { Provenance, ProvenanceAuditEntry } from "../types";

/** The audit trail records `table_name`, not a sentence. This turns each
 * row into the thing that actually happened, which is what a reader of a
 * dossier is looking for — the raw table/action pair stays visible
 * underneath, so nothing is hidden behind the paraphrase. */
function describe(entry: ProvenanceAuditEntry): string {
  const { table_name: table, action, before, after } = entry;
  const status = (payload: Record<string, unknown> | null): string | null => {
    const value = payload?.["status"];
    return typeof value === "string" ? value.replace(/_/g, " ") : null;
  };

  if (action === "transition") {
    const from = status(before);
    const to = status(after);
    const move = from && to ? `${from} → ${to}` : (to ?? "moved");
    if (table === "sample") return `Sample ${move}`;
    if (table === "batch") return `Batch ${move}`;
    if (table === "crucible") return `Crucible ${move}`;
    return `${table} ${move}`;
  }

  if (action === "amend") {
    if (table === "fire_assay_result") return "Result corrected";
    if (table === "certificate") return "Certificate amended";
    return `${table} amended`;
  }

  switch (table) {
    case "submission":
      return "Submission received";
    case "sample":
      return "Sample logged in";
    case "batch":
      return "Batch opened";
    case "crucible":
      return "Charged into a crucible";
    case "fire_assay_result":
      return "Result entered";
    case "certificate":
      return "Certificate issued";
    default:
      return `${table} ${action}`;
  }
}

function Field({ label, value }: { label: string; value: string | null }) {
  if (value === null) return null;
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

export function SampleProvenance() {
  const { id } = useParams<{ id: string }>();
  const [dossier, setDossier] = useState<Provenance | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setDossier(null);
    setError(null);
    getSampleProvenance(Number(id))
      .then((data) => {
        if (!cancelled) setDossier(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError && err.status === 404
            ? `No sample with id ${id}.`
            : "Could not load this dossier.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <main>
      <p>
        <Link to={`/samples/${id}`}>&larr; Back to sample</Link>
      </p>

      {error && <p className="error">{error}</p>}
      {!dossier && !error && <p className="muted">Loading…</p>}

      {dossier && (
        <>
          <header>
            <h1>{dossier.sample.sample_id}</h1>
            <p className="lede">
              Evidence dossier · <StatusPill status={dossier.sample.status} /> ·{" "}
              {dossier.client.code}
              {dossier.project ? ` · ${dossier.project.name}` : ""}
            </p>
          </header>

          <section>
            <h2>Seal</h2>
            <p className="muted">
              A <code>sha256</code> over every fact below, in canonical form. Anyone holding this
              dossier can recompute it without asking this server — it proves the bundle is
              internally consistent and has not been edited in transit.
            </p>
            <p className="seal">{dossier.seal}</p>
          </section>

          <section>
            <h2>Chain of custody</h2>
            <ol className="timeline">
              {dossier.audit_entries.map((entry) => (
                <li key={entry.id} className="timeline-item">
                  <div className="timeline-what">{describe(entry)}</div>
                  <div className="timeline-meta">
                    {entry.recorded_at} · {entry.actor ?? "system"} ·{" "}
                    <span className="muted">
                      {entry.table_name}#{entry.record_id} {entry.action}
                    </span>
                  </div>
                  {entry.reason && <div className="timeline-reason">“{entry.reason}”</div>}
                  <div className="timeline-hash" title="This entry's position in the audit chain">
                    {entry.entry_hash.slice(0, 16)}…
                  </div>
                </li>
              ))}
            </ol>
            {dossier.audit_entries.length === 0 && (
              <p className="muted">No recorded events yet.</p>
            )}
          </section>

          <section>
            <h2>Furnace</h2>
            {dossier.crucibles.length === 0 ? (
              <p className="muted">Never charged into a batch.</p>
            ) : (
              dossier.crucibles.map((crucible) => (
                <dl className="detail-grid" key={crucible.id}>
                  <Field label="Batch" value={crucible.batch_number} />
                  <Field label="Position" value={crucible.position} />
                  <Field label="Flux recipe" value={crucible.flux_recipe} />
                  <Field label="Portion" value={`${crucible.sample_weight_g ?? "—"} g`} />
                  <Field label="Charged" value={crucible.charged_at} />
                  <Field label="Lead button" value={nullableMg(crucible.lead_button_weight_mg)} />
                  <Field label="Prill" value={nullableMg(crucible.prill_weight_mg)} />
                  <Field
                    label="Parting acid"
                    value={crucible.parting_acid_volume_ml && `${crucible.parting_acid_volume_ml} mL`}
                  />
                  <Field label="Gold bead" value={nullableMg(crucible.gold_bead_mg)} />
                  <Field label="Weighed" value={crucible.weighed_at} />
                </dl>
              ))
            )}
          </section>

          <section>
            <h2>Result chain</h2>
            {dossier.results.length === 0 ? (
              <p className="muted">No result yet.</p>
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Grade</th>
                      <th>Bead</th>
                      <th>Portion</th>
                      <th>Analyst</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dossier.results.map((result) => {
                      const superseded = dossier.results.some(
                        (other) => other.supersedes_id === result.id,
                      );
                      return (
                        <tr key={result.id} className={superseded ? "superseded" : undefined}>
                          <td>{result.id}</td>
                          <td className="grade">
                            {result.au_censored
                              ? `<${result.au_detection_limit} ${result.au_unit}`
                              : `${result.au_value} ${result.au_unit}`}
                          </td>
                          <td>{nullableMg(result.gold_bead_mg)}</td>
                          <td>{result.sample_weight_g ? `${result.sample_weight_g} g` : "—"}</td>
                          <td className="muted">{result.analyst ?? "—"}</td>
                          <td className="muted">
                            {superseded ? "superseded" : "current"}
                            {result.superseded_reason && (
                              <> — corrected: “{result.superseded_reason}”</>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section>
            <h2>Certificates</h2>
            {dossier.certificates.length === 0 ? (
              <p className="muted">Not yet on a certificate.</p>
            ) : (
              <ul className="certificate-list">
                {dossier.certificates.map((certificate) => (
                  <li key={certificate.id}>
                    <strong>{certificate.certificate_number}</strong> · issued{" "}
                    {certificate.issued_at} by {certificate.issued_by ?? "—"}
                    <div className="muted">
                      reported result #{certificate.certified_result_id} · pdf sha256{" "}
                      {certificate.pdf_sha256.slice(0, 16)}…
                    </div>
                    <a href={`/api/certificates/${certificate.id}/pdf`}>Download PDF</a>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </main>
  );
}

function nullableMg(value: string | null): string {
  return value === null ? "—" : `${value} mg`;
}
