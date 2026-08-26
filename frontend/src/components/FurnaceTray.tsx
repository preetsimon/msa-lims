import { Link } from "react-router-dom";

import type { CrucibleSlot } from "../types";

/**
 * A digital twin of the physical furnace grid: `rows` × `columns` cells,
 * each showing whatever crucible sits at that position — or nothing, if the
 * slot has never been charged. Renders every cell in the tray, not just the
 * occupied ones, so an empty batch still shows the tray it will eventually
 * fill, matching how a technician reads the real one.
 *
 * Two write actions live inline in a cell, not the label link: charging an
 * empty slot (only offered while the batch is genuinely `charging` — the
 * server refuses it otherwise) and, once a crucible is `cupelled` or
 * `parted`, the next hand-driven measurement. All three callbacks are
 * optional so a read-only render (none supplied) degrades to exactly the
 * original display-only tray.
 */
export function FurnaceTray({
  rows,
  columns,
  crucibles,
  batchStatus,
  onChargeSlot,
  onPartCrucible,
  onWeighCrucible,
}: {
  rows: number;
  columns: number;
  crucibles: CrucibleSlot[];
  batchStatus?: string;
  onChargeSlot?: (row: number, col: number) => void;
  onPartCrucible?: (slot: CrucibleSlot) => void;
  onWeighCrucible?: (slot: CrucibleSlot) => void;
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

  const chargingOpen = batchStatus === "charging" && onChargeSlot !== undefined;

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
            {slot && slot.status === "cupelled" && onPartCrucible && (
              <button
                type="button"
                className="tray-action"
                onClick={() => onPartCrucible(slot)}
              >
                Record parting &rarr;
              </button>
            )}
            {slot && slot.status === "parted" && onWeighCrucible && (
              <button
                type="button"
                className="tray-action"
                onClick={() => onWeighCrucible(slot)}
              >
                Record weighing &rarr;
              </button>
            )}
            {!slot && chargingOpen && (
              <button
                type="button"
                className="tray-cell-charge"
                onClick={() => onChargeSlot?.(row, col)}
              >
                + Charge
              </button>
            )}
            {!slot && !chargingOpen && <span className="muted">—</span>}
          </div>
        );
      })}
    </div>
  );
}
