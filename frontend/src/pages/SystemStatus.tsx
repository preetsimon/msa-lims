import { useEffect, useState } from "react";

import { StatusPill } from "../components/StatusPill";
import type { ComponentHealth, HealthResponse } from "../types";

/**
 * The walking skeleton's original screen: is the stack up, and what is up
 * about it. Kept reachable from the nav for debugging, no longer the
 * landing page now that there is something more useful to land on.
 */
export function SystemStatus() {
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
        <h1>System status</h1>
        <p className="lede">Fire assay and geochemical laboratory information management.</p>
      </header>

      <section>
        {error && <p className="error">{error}</p>}
        {!health && !error && <p className="muted">Checking…</p>}
        {health && (
          <>
            <p>
              Overall: <StatusPill status={health.status} /> · version {health.version}
            </p>
            <dl className="detail-grid">
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
