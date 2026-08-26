import { useState } from "react";

import { ApiError, partCrucible } from "../api";
import { datetimeLocalValueToIso, nowAsDatetimeLocalValue } from "../datetimeInput";
import { Modal } from "./Modal";

/** Records one cupelled crucible's parting (button, prill, acid), the move
 * from `CUPELLED` to `PARTED`. */
export function PartCrucibleModal({
  batchId,
  crucibleId,
  label,
  onClose,
  onParted,
}: {
  batchId: number;
  crucibleId: number;
  label: string;
  onClose: () => void;
  onParted: () => void;
}) {
  const [leadButtonWeightMg, setLeadButtonWeightMg] = useState("");
  const [prillWeightMg, setPrillWeightMg] = useState("");
  const [partingAcidVolumeMl, setPartingAcidVolumeMl] = useState("");
  const [partedAt, setPartedAt] = useState(nowAsDatetimeLocalValue());
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitError(null);

    if (
      !leadButtonWeightMg ||
      Number(leadButtonWeightMg) <= 0 ||
      !prillWeightMg ||
      Number(prillWeightMg) <= 0 ||
      !partingAcidVolumeMl ||
      Number(partingAcidVolumeMl) <= 0
    ) {
      setSubmitError("Button weight, prill weight, and acid volume must all be greater than zero.");
      return;
    }

    setSubmitting(true);
    try {
      await partCrucible(batchId, crucibleId, {
        lead_button_weight_mg: leadButtonWeightMg,
        prill_weight_mg: prillWeightMg,
        parting_acid_volume_ml: partingAcidVolumeMl,
        parted_at: datetimeLocalValueToIso(partedAt),
      });
      onParted();
      onClose();
    } catch (err: unknown) {
      setSubmitError(err instanceof ApiError ? err.message : "Recording parting failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`Record parting — ${label}`} onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <div className="form-field">
          <label htmlFor="part-button">Lead button weight (mg)</label>
          <input
            id="part-button"
            type="number"
            step="any"
            min="0"
            value={leadButtonWeightMg}
            onChange={(event) => setLeadButtonWeightMg(event.target.value)}
          />
        </div>

        <div className="form-field">
          <label htmlFor="part-prill">Prill weight (mg)</label>
          <input
            id="part-prill"
            type="number"
            step="any"
            min="0"
            value={prillWeightMg}
            onChange={(event) => setPrillWeightMg(event.target.value)}
          />
        </div>

        <div className="form-field">
          <label htmlFor="part-acid">Parting acid volume (mL)</label>
          <input
            id="part-acid"
            type="number"
            step="any"
            min="0"
            value={partingAcidVolumeMl}
            onChange={(event) => setPartingAcidVolumeMl(event.target.value)}
          />
        </div>

        <div className="form-field">
          <label htmlFor="part-time">Parted at</label>
          <input
            id="part-time"
            type="datetime-local"
            value={partedAt}
            onChange={(event) => setPartedAt(event.target.value)}
          />
        </div>

        {submitError && <p className="error">{submitError}</p>}

        <div className="form-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={submitting}>
            {submitting ? "Recording…" : "Record parting"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
