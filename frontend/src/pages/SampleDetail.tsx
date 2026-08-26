import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, getSample } from "../api";
import { StatusPill } from "../components/StatusPill";
import { formatMeasured, type SampleDetail as SampleDetailData } from "../types";

export function SampleDetail() {
  const { id } = useParams<{ id: string }>();
  const [sample, setSample] = useState<SampleDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setSample(null);
    setError(null);
    getSample(Number(id))
      .then((data) => {
        if (!cancelled) setSample(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError && err.status === 404
            ? `No sample with id ${id}.`
            : "Could not load this sample.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <main>
      <p>
        <Link to="/samples">&larr; All samples</Link>
      </p>

      {error && <p className="error">{error}</p>}
      {!sample && !error && <p className="muted">Loading…</p>}

      {sample && (
        <>
          <header>
            <h1>{sample.sample_id}</h1>
            <p className="lede">
              <StatusPill status={sample.status} /> · {sample.sample_type} ·{" "}
              <Link to={`/samples/${sample.id}/provenance`}>Evidence dossier</Link>
            </p>
          </header>

          <section>
            <h2>Details</h2>
            <dl className="detail-grid">
              <dt>Submission</dt>
              <dd>#{sample.submission_id}</dd>
              {sample.drill_hole_id !== null && (
                <>
                  <dt>Drill hole</dt>
                  <dd>#{sample.drill_hole_id}</dd>
                </>
              )}
              {sample.from_depth_m !== null && sample.to_depth_m !== null && (
                <>
                  <dt>Interval</dt>
                  <dd>
                    {sample.from_depth_m}–{sample.to_depth_m} m
                  </dd>
                </>
              )}
            </dl>
          </section>

          <section>
            <h2>Fire assay result</h2>
            {sample.current_result ? (
              <dl className="detail-grid">
                <dt>Au</dt>
                <dd className="grade">{formatMeasured(sample.current_result.au)}</dd>
                <dt>Method</dt>
                <dd className="muted">{sample.current_result.method}</dd>
                <dt>Bead weight</dt>
                <dd>{sample.current_result.gold_bead_mg} mg</dd>
                <dt>Portion</dt>
                <dd>{sample.current_result.sample_weight_g} g</dd>
                {sample.current_result.crucible_id !== null && (
                  <>
                    <dt>Crucible</dt>
                    <dd className="muted">#{sample.current_result.crucible_id}</dd>
                  </>
                )}
                <dt>Analysed</dt>
                <dd className="muted">{sample.current_result.analysed_at}</dd>
              </dl>
            ) : (
              <p className="muted">No result yet.</p>
            )}
          </section>

          <section>
            <h2>Certificates</h2>
            {sample.certificates.length === 0 ? (
              <p className="muted">Not yet on a certificate.</p>
            ) : (
              <ul className="certificate-list">
                {sample.certificates.map((certificate) => (
                  <li key={certificate.id}>
                    {certificate.certificate_number}
                    {" — "}
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
