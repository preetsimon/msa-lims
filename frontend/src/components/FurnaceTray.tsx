import { Link } from "react-router-dom";

import type { CrucibleSlot } from "../types";

/**
 * A digital twin of the physical furnace grid: `rows` × `columns` cells,
 * each showing whatever crucible sits at that position — or nothing, if the
 * slot has never been charged. Renders every cell in the tray, not just the
 * occupied ones, so an empty batch still shows the tray it will eventually
 * fill, matching how a technician reads the real one.
 */
export function FurnaceTray({
  rows,
  columns,
  crucibles,
}: {
  rows: number;
  columns: number;
  crucibles: CrucibleSlot[];
}) {
  const byPosition = new Map<string, CrucibleSlot>();
  for (const slot of crucibles) {
    byPosition.set(`${slot.position_row}-${slot.position_col}`, slot);
  }

  const positions: { row: number; col: number }[] = [];
  for (let row = 1; row <= rows; row++) {
    for (let col = 1; col <= columns; col++) {
      positions.push({ row, col });
    }
  }

  return (
    <div className="tray-grid" style={{ gridTemplateColumns: `repeat(${columns}, 1fr)` }}>
      {positions.map(({ row, col }) => {
        const slot = byPosition.get(`${row}-${col}`);
        return (
          <div
            key={`${row}-${col}`}
            className={slot ? `tray-cell tray-cell-${slot.status}` : "tray-cell tray-cell-empty"}
          >
            <span className="tray-position">
              {row}-{col}
            </span>
            {slot && slot.sample_id !== null && (
              <Link to={`/samples/${slot.sample_id}`} className="tray-label">
                {slot.sample_label}
              </Link>
            )}
            {slot && slot.qc_material_id !== null && (
              <span className="tray-qc-badge">{slot.qc_material_type}</span>
            )}
            {slot && <span className="muted tray-position">{slot.status.replace(/_/g, " ")}</span>}
            {!slot && <span className="muted">—</span>}
          </div>
        );
      })}
    </div>
  );
}
