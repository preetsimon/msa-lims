/**
 * Renders any status string as a coloured pill. Shared across the health
 * check and the sample screens — see `index.css` for which statuses map to
 * which of the three colour tiers (`pill-ok`/`pill-warn`/`pill-bad` families).
 */
export function StatusPill({ status }: { status: string }) {
  return <span className={`pill pill-${status}`}>{status.replace(/_/g, " ")}</span>;
}
