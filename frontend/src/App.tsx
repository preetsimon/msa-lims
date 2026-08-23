import { useEffect, useState } from "react";

import type { ComponentHealth, HealthResponse } from "./types";

/**
 * The walking skeleton's one screen: is the stack up, and what is up about it.
 *
 * It exists to prove the whole path end to end — Vite dev server, proxy,
 * FastAPI, Postgres — before any real screen is built on top of it. The sample
 * manager replaces this in Phase 1.
 */
export function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/health")
      .then((response) => response.json() as Promise<HealthResponse>)
      .then((body) => {
        if (!cancelled) setHealth(body);
      })
      .catch(() => {
        if (!cancelled) setError("The API is not answering on port 8002.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main>
      <header>
        <h1>MSA LIMS</h1>
        <p className="lede">
          Fire assay and geochemical laboratory information management.
        </p>
      </header>

      <section>
        <h2>System status</h2>
        {error && <p className="error">{error}</p>}
        {!health && !error && <p className="muted">Checking…</p>}
        {health && (
          <>
            <p>
              Overall: <StatusPill status={health.status} /> · version {health.version}
            </p>
            <dl className="components">
              <Component label="Database" health={health.database} />
              <Component label="QC Sentinel" health={health.qc_sentinel} />
            </dl>
          </>
        )}
      </section>
    </main>
  );
}

function Component({ label, health }: { label: string; health: ComponentHealth }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>
        <StatusPill status={health.status} />
        {health.detail && <span className="muted"> — {health.detail}</span>}
      </dd>
    </>
  );
}

function StatusPill({ status }: { status: string }) {
  return <span className={`pill pill-${status}`}>{status.replace(/_/g, " ")}</span>;
}
