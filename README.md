# MSA LIMS

Laboratory information management for fire assay and geochemical analysis:
sample custody, preparation, furnace batching, results, and certificates of
analysis.

MSA LIMS is the **system of record**. Quality-control surveillance is a separate
system — [QC Sentinel](../fireAssay) — which reads QC results exported from here
and returns advisory verdicts. Sentinel never writes to this database, and
nothing it concludes can block a result from being reported. That separation is
deliberate and is the point of the architecture, not an accident of history.

## Three things that define the design

**A result is not a float.** `<0.01 g/t` is a statement that the method could
not see the analyte, and it is a different statement from `0`. The
`MeasuredValue` type has no `__float__`, so a non-detect cannot drift into a
mean, a composite, or a certificate by accident — code that needs a number must
either assert the value was detected or name the substitution convention it is
using, and that choice then appears in the diff.

**The gold bead and the doré bead are different weights.** Cupellation leaves a
bead of gold *and silver*; parting dissolves the silver away. Reporting the
pre-parting weight as gold inflates every grade by the silver content, and it is
the classic error in this calculation. `domain/assay.py` takes them as two named
parameters with no default between them, so making that mistake requires typing
it deliberately.

**History cannot be rewritten, and Postgres is what guarantees it.** The
application connects as `msa_app`, a role holding no UPDATE or DELETE on
`audit_event`. An integration test takes those exact credentials and tries to
tamper with a stored event; the database refuses it. Application discipline
would be one careless `session.merge()` from being untrue.

## Quick start

```bash
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev,pdf,oidc]"
docker compose up -d                     # postgres :5435
.venv/bin/alembic upgrade head           # schema + append-only grants
.venv/bin/python -m pytest               # 127 tests
```

The `Makefile` wraps these as `make install`, `make services`, `make migrate`
and `make check`.

`.venv/bin/python -m pytest -m "not integration"` runs the unit and property
suites with no services required.

## Running it

```bash
make run
```

```bash
make ui
```

The API is at `http://localhost:8002` with docs at `/docs`; the interface is at
`http://localhost:5175` and proxies `/api` and `/health` to the backend, so the
browser sees one origin and there is no CORS configuration to get wrong.

## Layout

| Path | What lives there |
|---|---|
| `src/msa_lims/domain/` | Pure domain core — units, censored values, sample identity, assay arithmetic, the sample state machine. No I/O, no session, no clock. |
| `src/msa_lims/db/` | SQLAlchemy models, session management, constraint naming. |
| `src/msa_lims/web/` | FastAPI app, routes, error-code mapping. |
| `migrations/` | Alembic revisions, including the append-only grants. |
| `frontend/` | React + TypeScript + Vite interface. |
| `tests/unit`, `tests/property`, `tests/integration` | Example-based, Hypothesis, and real-Postgres suites. |

## Documentation

| Document | What it covers |
|---|---|
| [docs/ENGINEERING_GUIDE.md](docs/ENGINEERING_GUIDE.md) | Start here. Domain vocabulary, the principles, how to add things. |
| [PROGRESS.md](PROGRESS.md) | Current phase, what is built, decision log, next actions. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | The two-system topology and the QC Sentinel seam. |

## Status

**Phase 0 complete** — walking skeleton. Domain core, spine schema, append-only
grants, health endpoint, and the React shell all run end to end. Phase 1 builds
the first complete thread: register a submission, enter a result, issue a
certificate.
