import { useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  createFireAssayResult,
  createSolutionFinish,
  listInstruments,
  type Instrument,
} from "../api";

type FinishType = "gravimetric" | "solution";

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
  const [method, setMethod] = useState("AAS");
  const [concentration, setConcentration] = useState("");
  const [concUnit, setConcUnit] = useState("g/t");
  const [solutionVolumeMl, setSolutionVolumeMl] = useState("");
  const [detectionLimit, setDetectionLimit] = useState("");
  const [upperCalibLimit, setUpperCalibLimit] = useState("");

  const loadInstruments = () => {
    listInstruments({ instrument_type: finishType === "gravimetric" ? "BALANCE" : "AAS" }).then(
      setInstruments,
    );
  };

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
        navigate(`/samples/${sid}`);
      } else {
        await createSolutionFinish({
          sample_id: sid,
          method,
          concentration,
          concentration_unit: concUnit,
          ...(solutionVolumeMl ? { solution_volume_ml: solutionVolumeMl } : {}),
          ...(sampleWeightG ? { sample_weight_g: sampleWeightG } : {}),
          analysed_at: analysedAt,
          ...(detectionLimit ? { detection_limit: detectionLimit } : {}),
          ...(upperCalibLimit ? { upper_calibration_limit: upperCalibLimit } : {}),
          ...(notes ? { notes } : {}),
          ...(instrumentId ? { instrument_id: Number(instrumentId) } : {}),
        });
        navigate(`/samples/${sid}`);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: 24, maxWidth: 600 }}>
      <h1>Enter Fire Assay Result</h1>
      {error && <div className="page-error">{error}</div>}

      <div style={{ marginBottom: 16 }}>
        <label>
          Finish Type{" "}
          <select
            value={finishType}
            onChange={(e) => {
              setFinishType(e.target.value as FinishType);
              setInstrumentId("");
              loadInstruments();
            }}
          >
            <option value="gravimetric">Gravimetric (Fire Assay)</option>
            <option value="solution">Solution (AAS / ICP-MS)</option>
          </select>
        </label>
      </div>

      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <label>
          Sample ID *
          <input
            value={sampleId}
            onChange={(e) => setSampleId(e.target.value)}
            required
            placeholder="e.g. 23-0001-001-Au-01"
          />
        </label>

        {finishType === "gravimetric" ? (
          <>
            <label>
              Gold Bead (mg) *
              <input
                value={goldBeadMg}
                onChange={(e) => setGoldBeadMg(e.target.value)}
                required
                type="number"
                step="0.001"
              />
            </label>
            <label>
              Sample Weight (g) *
              <input
                value={sampleWeightG}
                onChange={(e) => setSampleWeightG(e.target.value)}
                required
                type="number"
                step="0.0001"
              />
            </label>
            <label>
              Balance Sensitivity (mg)
              <input
                value={sensitivityMg}
                onChange={(e) => setSensitivityMg(e.target.value)}
                type="number"
                step="0.001"
              />
            </label>
            <label>
              Doré Bead (mg)
              <input
                value={doreBeadMg}
                onChange={(e) => setDoreBeadMg(e.target.value)}
                type="number"
                step="0.001"
              />
            </label>
          </>
        ) : (
          <>
            <label>
              Method *
              <select value={method} onChange={(e) => setMethod(e.target.value)}>
                <option value="AAS">AAS</option>
                <option value="ICP-MS">ICP-MS</option>
              </select>
            </label>
            <label>
              Concentration *
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
              <select value={concUnit} onChange={(e) => setConcUnit(e.target.value)}>
                <option value="g/t">g/t</option>
                <option value="ppm">ppm</option>
                <option value="ppb">ppb</option>
                <option value="%">%</option>
              </select>
            </label>
            <label>
              Solution Volume (mL)
              <input
                value={solutionVolumeMl}
                onChange={(e) => setSolutionVolumeMl(e.target.value)}
                type="number"
                step="0.01"
              />
            </label>
            <label>
              Detection Limit
              <input
                value={detectionLimit}
                onChange={(e) => setDetectionLimit(e.target.value)}
                type="number"
                step="0.001"
              />
            </label>
            <label>
              Upper Calibration Limit
              <input
                value={upperCalibLimit}
                onChange={(e) => setUpperCalibLimit(e.target.value)}
                type="number"
                step="0.001"
              />
            </label>
          </>
        )}

        <label>
          Sample Weight (g) (optional)
          <input
            value={sampleWeightG}
            onChange={(e) => setSampleWeightG(e.target.value)}
            type="number"
            step="0.0001"
          />
        </label>

        <label>
          Instrument
          <select value={instrumentId} onChange={(e) => setInstrumentId(e.target.value)}>
            <option value="">— None —</option>
            {instruments.map((i) => (
              <option key={i.id} value={i.id}>
                {i.name} ({i.instrument_type})
              </option>
            ))}
          </select>
        </label>

        <label>
          Analysed At *
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

        <button type="submit" disabled={loading}>
          {loading ? "Saving..." : "Submit Result"}
        </button>
      </form>
    </div>
  );
}
