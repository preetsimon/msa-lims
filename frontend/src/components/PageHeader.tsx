import type { ReactNode } from "react";

/** The standard page title block: an `h1` with an optional one-line lede.
 * Every list and detail page opens with one of these, so the heading
 * rhythm and margin live in one place rather than being re-guessed per
 * page. `children` renders under the lede — used for the primary action a
 * page owns (e.g. "Register instrument"). */
export function PageHeader({
  title,
  lede,
  children,
}: {
  title: string;
  lede?: string;
  children?: ReactNode;
}) {
  return (
    <header>
      <h1>{title}</h1>
      {lede ? <p className="lede">{lede}</p> : null}
      {children}
    </header>
  );
}
