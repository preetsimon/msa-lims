import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, getSubmission, listClients, type ClientListItem } from "../api";
import { PageHeader } from "../components/PageHeader";
import { StatusPill } from "../components/StatusPill";
import type { Submission } from "../types";

export function SubmissionDetail() {
  const { id } = useParams<{ id: string }>();
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [clients, setClients] = useState<ClientListItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listClients()
      .then(setClients)
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setSubmission(null);
    setError(null);
    getSubmission(Number(id))
      .then((data) => {
        if (!cancelled) setSubmission(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError && err.status === 404
            ? `No submission with id ${id}.`
            : "Could not load this submission.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const clientName = (clientId: number) =>
    clients.find((c) => c.id === clientId)?.name ?? `Client #${clientId}`;

  return (
    <main>
      <p>
        <Link to="/submissions">&larr; All submissions</Link>
      </p>

      {error && <p className="page-error">{error}</p>}
      {!submission && !error && <p className="muted">Loading…</p>}

      {submission && (
        <>
          <PageHeader
            title={submission.submission_number}
            lede={`Received ${new Date(submission.received_at).toLocaleString()}`}
          />

          <section>
            <h2>Details</h2>
            <dl className="detail-grid">
              <dt>Client</dt>
              <dd>{clientName(submission.client_id)}</dd>
              {submission.project_id !== null && (
                <>
                  <dt>Project</dt>
                  <dd className="muted">#{submission.project_id}</dd>
                </>
              )}
              <dt>Samples</dt>
              <dd>{submission.samples.length}</dd>
              {submission.declared_sample_count !== null && (
                <>
                  <dt>Declared count</dt>
                  <dd className="muted">{submission.declared_sample_count}</dd>
                </>
              )}
            </dl>
          </section>

          <section>
            <h2>Samples</h2>
            {submission.samples.length === 0 ? (
              <p className="empty-state">No samples logged in yet.</p>
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Sample</th>
                      <th>Type</th>
                      <th>Status</th>
                      <th>Interval (m)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {submission.samples.map((sample) => (
                      <tr key={sample.id}>
                        <td>
                          <Link to={`/samples/${sample.id}`}>{sample.sample_id}</Link>
                        </td>
                        <td className="muted">{sample.sample_type}</td>
                        <td>
                          <StatusPill status={sample.status} />
                        </td>
                        <td className="muted">
                          {sample.from_depth_m !== null && sample.to_depth_m !== null
                            ? `${sample.from_depth_m}–${sample.to_depth_m}`
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </main>
  );
}
