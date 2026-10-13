import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, getCertificate } from "../api";
import { PageHeader } from "../components/PageHeader";
import { formatMeasured, type CertificateDetail as CertificateDetailData } from "../types";

export function CertificateDetail() {
  const { id } = useParams<{ id: string }>();
  const [certificate, setCertificate] = useState<CertificateDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setCertificate(null);
    setError(null);
    getCertificate(Number(id))
      .then((data) => {
        if (!cancelled) setCertificate(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError && err.status === 404
            ? `No certificate with id ${id}.`
            : "Could not load this certificate.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <main>
      <p>
        <Link to="/certificates">&larr; All certificates</Link>
      </p>

      {error && <p className="page-error">{error}</p>}
      {!certificate && !error && <p className="muted">Loading…</p>}

      {certificate && (
        <>
          <PageHeader
            title={certificate.certificate_number}
            lede={`Issued ${new Date(certificate.issued_at).toLocaleString()}`}
          >
            <p>
              {certificate.is_stale ? (
                <span className="pill pill-warn">Superseded</span>
              ) : (
                <span className="pill pill-ok">Current</span>
              )}{" "}
              <a
                href={`/api/certificates/${certificate.id}/pdf`}
                target="_blank"
                rel="noreferrer"
              >
                Download PDF
              </a>
            </p>
          </PageHeader>

          <section>
            <h2>Details</h2>
            <dl className="detail-grid">
              <dt>Client</dt>
              <dd>#{certificate.client_id}</dd>
              <dt>Issued by</dt>
              <dd className="muted">#{certificate.issued_by_id}</dd>
              <dt>PDF SHA-256</dt>
              <dd>
                <code>{certificate.pdf_sha256}</code>
              </dd>
              {certificate.supersedes_id !== null && (
                <>
                  <dt>Supersedes</dt>
                  <dd className="muted">#{certificate.supersedes_id}</dd>
                </>
              )}
              {certificate.superseded_reason !== null && (
                <>
                  <dt>Superseded reason</dt>
                  <dd className="muted">{certificate.superseded_reason}</dd>
                </>
              )}
              {certificate.notes !== null && (
                <>
                  <dt>Notes</dt>
                  <dd className="muted">{certificate.notes}</dd>
                </>
              )}
            </dl>
          </section>

          <section>
            <h2>Certified samples</h2>
            {certificate.samples.length === 0 ? (
              <p className="empty-state">This certificate names no samples.</p>
            ) : (
              certificate.samples.map((sample) => (
                <div key={sample.fire_assay_result_id} className="card">
                  <h3>
                    <Link to={`/samples/${sample.sample_id}`}>{sample.sample_label}</Link>
                  </h3>
                  <dl className="detail-grid">
                    <dt>Au</dt>
                    <dd className="grade">{formatMeasured(sample.au)}</dd>
                    <dt>Method</dt>
                    <dd className="muted">{sample.method}</dd>
                    {sample.digest_method != null && (
                      <>
                        <dt>Digest</dt>
                        <dd className="muted">{sample.digest_method.replace(/_/g, " ")}</dd>
                      </>
                    )}
                  </dl>
                  {sample.elements.length > 0 && (
                    <div className="table-wrap">
                      <table className="data-table">
                        <thead>
                          <tr>
                            <th>Element</th>
                            <th>Grade</th>
                            <th>Unit</th>
                            <th>Detection limit</th>
                            <th>Digest</th>
                          </tr>
                        </thead>
                        <tbody>
                          {sample.elements.map((el) => (
                            <tr key={el.element}>
                              <td>{el.element}</td>
                              <td className="grade">{el.grade_value}</td>
                              <td className="muted">{el.grade_unit}</td>
                              <td className="muted">{el.detection_limit ?? "—"}</td>
                              <td className="muted">{el.digest_method.replace(/_/g, " ")}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              ))
            )}
          </section>
        </>
      )}
    </main>
  );
}
