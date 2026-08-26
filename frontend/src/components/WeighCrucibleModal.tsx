import { useState } from "react";

import { ApiError, weighCrucible } from "../api";
import { datetimeLocalValueToIso, nowAsDatetimeLocalValue } from "../datetimeInput";
import { Modal } from "./Modal";

/** Records one parted crucible's final gold-bead weighing, the move from
 * `PARTED` to `WEIGHED`. Zero is a legal bead weight (a true non-detect), so
 * only a negative value is refused. */
export function WeighCrucibleModal({
  batchId,
  crucibleId,
  label,
  onClose,
  onWeighed,
}: {
  batchId: number;
  crucibleId: number;
  label: string;
  onClose: () => void;
  onWeighed: () => void;
}) {
  const [goldBeadMg, setGoldBeadMg] = useState("");
  const [weighedAt, setWeighedAt] = useState(nowAsDatetimeLocalValue());
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitError(null);

    if (goldBeadMg === "" || Number(goldBeadMg) < 0) {
      setSubmitError("Gold bead weight must be zero or greater.");
      return;
    }

    setSubmitting(true);
    try {
      await weighCrucible(batchId, crucibleId, {
        gold_bead_mg: goldBeadMg,
        weighed_at: datetimeLocalValueToIso(weighedAt),
      });
      onWeighed();
      onClose();
    } catch (err: unknown) {
      setSubmitError(err instanceof ApiError ? err.message : "Recording the weighing failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`Record weighing — ${label}`} onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <div className="form-field">
          <label htmlFor="weigh-bead">Gold bead weight (mg)</label>
          <input
            id="weigh-bead"
            type="number"
            step="any"
            min="0"
            value={goldBeadMg}
            onChange={(event) => setGoldBeadMg(event.target.value)}
          />
        </div>

        <div className="form-field">
          <label htmlFor="weigh-time">Weighed at</label>
          <input
            id="weigh-time"
            type="datetime-local"
            value={weighedAt}
            onChange={(event) => setWeighedAt(event.target.value)}
          />
        </div>

        {submitError && <p className="error">{submitError}</p>}

        <div className="form-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={submitting}>
            {submitting ? "Recording…" : "Record weighing"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
