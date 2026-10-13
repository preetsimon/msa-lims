import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  createFireAssayResult,
  createSolutionFinish,
  listInstruments,
  type Instrument,
} from "../api";
import { PageHeader } from "../components/PageHeader";

type FinishType = "gravimetric" | "solution";

/** Which instrument types can back each finish. Gravimetric gold is
 * weighed, so it needs a balance; a solution finish is measured on an
 * atomic/ICP spectrometer. */
const GRAVIMETRIC_TYPES = ["microbalance"];
const SOLUTION_TYPES = ["aas", "icp_ms", "icp_oes"];

export function ResultEntryPage() {
  const navigate = useNavigate();
  const [finishType, setFinishType] = useState<FinishType>("gravimetric");
  const [sampleId, setSampleId] = useState("");
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [instrumentId, setInstrumentId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Gravimetric fields
  const [goldBeadMg, setGoldBeadMg] = useState("");
  const [sampleWeightG, setSampleWeightG] = useState("");
  const [sensitivityMg, setSensitivityMg] = useState("");
  const [doreBeadMg, setDoreBeadMg] = useState("");
  const [analysedAt, setAnalysedAt] = useState(new Date().toISOString().slice(0, 16));
  const [notes, setNotes] = useState("");

  // Solution fields
  const [method, setMethod] = useState<"fire_assay_aas" | "fire_assay_icp_ms">(
    "fire_assay_aas",
  );
  const [concentration, setConcentration] = useState("");
  const [concUnit, setConcUnit] = useState<"mg/L" | "ug/L">("mg/L");
  const [solutionVolumeMl, setSolutionVolumeMl] = useState("");
  const [detectionLimit, setDetectionLimit] = useState("");
  const [upperCalibLimit, setUpperCalibLimit] = useState("");

  // The full registry, fetched once; the dropdown narrows it to the
  // instruments that can actually back the selected finish.
  useEffect(() => {
    listInstruments()
      .then(setInstruments)
      .catch(() => setInstruments([]));
  }, []);

  const relevantTypes = finishType === "gravimetric" ? GRAVIMETRIC_TYPES : SOLUTION_TYPES;
  const relevantInstruments = instruments.filter((i) => relevantTypes.includes(i.instrument_type));
  const selectedInstrument =
    instruments.find((i) => i.id === Number(instrumentId)) ?? null;

  // Completing the instrument registry's promise: the detection limit and
  // balance sensitivity are properties of the instrument, not of a single
  // reading. Selecting an instrument pre-fills them; the analyst can still
  // override for a one-off, and the backend falls back to the instrument's
  // own calibration when the field is left blank.
  useEffect(() => {
    if (!selectedInstrument) return;
    if (finishType === "gravimetric" && selectedInstrument.balance_sensitivity_mg) {
      setSensitivityMg(selectedInstrument.balance_sensitivity_mg);
    }
    if (finishType === "solution" && selectedInstrument.solution_detection_limit) {
      setDetectionLimit(selectedInstrument.solution_detection_limit);
    }
  }, [instrumentId, finishType]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!sampleId.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const sid = Number(sampleId);
      if (finishType === "gravimetric") {
        await createFireAssayResult({
          sample_id: sid,
          gold_bead_mg: goldBeadMg,
          sample_weight_g: sampleWeightG,
          ...(sensitivityMg ? { balance_sensitivity_mg: sensitivityMg } : {}),
          ...(doreBeadMg ? { dore_bead_mg: doreBeadMg } : {}),
          analysed_at: analysedAt,
          ...(notes ? { notes } : {}),
          ...(instrumentId ? { instrument_id: Number(instrumentId) } : {}),
        });
      } else {
        await createSolutionFinish({
          sample_id: sid,
          method,
          concentration,
          concentration_unit: concUnit,
          solution_volume_ml: solutionVolumeMl,
          ...(sampleWeightG ? { sample_weight_g: sampleWeightG } : {}),
          analysed_at: analysedAt,
          ...(detectionLimit ? { detection_limit: detectionLimit } : {}),
          ...(upperCalibLimit ? { upper_calibration_limit: upperCalibLimit } : {}),
          ...(notes ? { notes } : {}),
          ...(instrumentId ? { instrument_id: Number(instrumentId) } : {}),
        });
      }
      navigate(`/samples/${sid}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not submit the result.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main>
      <PageHeader
        title="Enter Result"
        lede="Record a fire-assay or solution finish against a sample."
      />

      {error && <p className="page-error">{error}</p>}

      <form className="card form-grid" onSubmit={handleSubmit}>
        <label>
          Finish type
          <select
            value={finishType}
            onChange={(e) => {
              setFinishType(e.target.value as FinishType);
              setInstrumentId("");
            }}
          >
            <option value="gravimetric">Gravimetric (fire assay)</option>
            <option value="solution">Solution (AAS / ICP-MS)</option>
          </select>
        </label>

        <label>
          Sample ID
          <input
            value={sampleId}
            onChange={(e) => setSampleId(e.target.value)}
            required
            placeholder="e.g. 23-0001-001-Au-01"
          />
        </label>

        <label>
          Instrument
          <select value={instrumentId} onChange={(e) => setInstrumentId(e.target.value)}>
            <option value="">— None —</option>
            {relevantInstruments.map((i) => (
              <option key={i.id} value={i.id}>
                {i.name} ({i.instrument_type})
              </option>
            ))}
          </select>
          {relevantInstruments.length === 0 && (
            <span className="field-hint">
              No {finishType === "gravimetric" ? "balance" : "spectrometer"} registered yet — you
              can still enter the result and pick one later.
            </span>
          )}
        </label>

        {finishType === "gravimetric" ? (
          <>
            <label>
              Gold bead (mg)
              <input
                value={goldBeadMg}
                onChange={(e) => setGoldBeadMg(e.target.value)}
                required
                type="number"
                step="0.001"
              />
            </label>
            <label>
              Sample weight (g)
              <input
                value={sampleWeightG}
                onChange={(e) => setSampleWeightG(e.target.value)}
                required
                type="number"
                step="0.0001"
              />
            </label>
            <label>
              Balance sensitivity (mg)
              <input
                value={sensitivityMg}
                onChange={(e) => setSensitivityMg(e.target.value)}
                type="number"
                step="0.001"
              />
              {selectedInstrument?.balance_sensitivity_mg ? (
                <span className="field-hint">
                  From {selectedInstrument.name}: {selectedInstrument.balance_sensitivity_mg} mg
                </span>
              ) : (
                <span className="field-hint">
                  Leave blank to use the instrument's own calibration.
                </span>
              )}
            </label>
            <label>
              Doré bead (mg)
              <input
                value={doreBeadMg}
                onChange={(e) => setDoreBeadMg(e.target.value)}
                type="number"
                step="0.001"
              />
              <span className="field-hint">Optional — enables silver by difference.</span>
            </label>
          </>
        ) : (
          <>
            <label>
              Method
              <select
                value={method}
                onChange={(e) =>
                  setMethod(e.target.value as "fire_assay_aas" | "fire_assay_icp_ms")
                }
              >
                <option value="fire_assay_aas">AAS</option>
                <option value="fire_assay_icp_ms">ICP-MS</option>
              </select>
            </label>
            <label>
              Concentration
              <input
                value={concentration}
                onChange={(e) => setConcentration(e.target.value)}
                required
                type="number"
                step="0.001"
              />
            </label>
            <label>
              Unit
              <select
                value={concUnit}
                onChange={(e) => setConcUnit(e.target.value as "mg/L" | "ug/L")}
              >
                <option value="mg/L">mg/L</option>
                <option value="ug/L">ug/L</option>
              </select>
              <span className="field-hint">
                Solution concentration as read by the instrument.
              </span>
            </label>
            <label>
              Sample weight (g)
              <input
                value={sampleWeightG}
                onChange={(e) => setSampleWeightG(e.target.value)}
                type="number"
                step="0.0001"
              />
              <span className="field-hint">Optional.</span>
            </label>
            <label>
              Solution volume (mL)
              <input
                value={solutionVolumeMl}
                onChange={(e) => setSolutionVolumeMl(e.target.value)}
                required
                type="number"
                step="0.01"
              />
              <span className="field-hint">
                The volume the dissolved bead was made up to.
              </span>
            </label>
            <label>
              Detection limit
              <input
                value={detectionLimit}
                onChange={(e) => setDetectionLimit(e.target.value)}
                type="number"
                step="0.001"
              />
              {selectedInstrument?.solution_detection_limit ? (
                <span className="field-hint">
                  From {selectedInstrument.name}: {selectedInstrument.solution_detection_limit}
                </span>
              ) : (
                <span className="field-hint">
                  Leave blank to use the instrument's own calibration.
                </span>
              )}
            </label>
            <label>
              Upper calibration limit
              <input
                value={upperCalibLimit}
                onChange={(e) => setUpperCalibLimit(e.target.value)}
                type="number"
                step="0.001"
              />
              <span className="field-hint">Optional.</span>
            </label>
          </>
        )}

        <label>
          Analysed at
          <input
            type="datetime-local"
            value={analysedAt}
            onChange={(e) => setAnalysedAt(e.target.value)}
            required
          />
        </label>

        <label>
          Notes
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} />
        </label>

        <div className="form-actions">
          <button type="submit" className="btn-primary" disabled={loading}>
            {loading ? "Saving…" : "Submit result"}
          </button>
        </div>
      </form>
    </main>
  );
}
