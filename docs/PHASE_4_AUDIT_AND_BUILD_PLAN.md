# MSA LIMS — Phase 4 Audit & Build Plan

**Written:** 2026-08-28 · **Audience:** whoever picks up this codebase next ·
**Scope:** commit `ca4ac3a` ("phase 4 and beyond") plus a plan through Phase 6

**Read [ENGINEERING_GUIDE.md](ENGINEERING_GUIDE.md) first** if you have not
worked in this codebase before — it explains the domain vocabulary and the
five principles everything here assumes you already hold (append-only by
grant, `Decimal` never `float`, censored values with no `__float__`, the
observe-recommend-record boundary, and "every number traces to a weighing").
This document does not re-explain those; it applies them to one commit's
worth of new code and then plans forward.

**How to use this doc.** Part I is what is broken and why — read it even if
you only intend to do the fix, because the *why* is what stops you
reintroducing the same bug somewhere else. Part II is Track A: an ordered,
copy-pasteable set of fixes that close Phase 4 out to the standard Phases
0–3 already hold. Part III is Track B: the Sentinel integration, Phase 5.
Part IV is Track C: a ranked pick-list for Phase 6 and beyond. Work Track A
top to bottom — the order is load-bearing, not arbitrary. Tracks B and C are
picked from once A is closed.

---

## Part I — What is broken, and why

### How this was found

Two facts before any code review: **the dev database was five migrations
behind `head`.** `alembic current` reported `c152c0ac35dc` — the
duplicate-insertion migration from three days before this commit. Every
Phase 4 migration (`a1b2c3d4e5f6` through `e5f6a7b8c9d0`) had never been
applied on this machine, meaning the Phase 4 schema had never met a running
Postgres before it was committed. Second: `make lint` fails on the
committed tree — `ruff check` passes but `ruff format --check` wants to
reformat 7 files, starting with the `Element` enum's migration. CI runs
both (`ci.yml:45`), so this commit red-builds.

Those two facts are the root cause, not a coincidence. Phase 4 is the
first slice in this project's history marked substantially complete
*without* the live end-to-end curl walk that PROGRESS.md records for every
other phase. Three of the findings below were reproduced by writing three
throwaway integration tests against the repo's own `app_session` fixture
and running them against a real Postgres instance — the same five minutes
of verification every earlier phase got, and this one didn't.

Findings are numbered F1–F11, ranked by severity. F1–F3 are proven against
a live database. F4–F6 are design defects verified by reading and grep.
F7–F11 are consistency and hygiene.

### F1 (critical) — The correction path cannot execute

**Where:** `db/models.py:917`, `migrations/versions/a1b2c3d4e5f6_multi_element_result.py:78`,
`multi_element/service.py:176`

`multi_element_result` carries:

```sql
UNIQUE (sample_id, element, digest_method)
```

But a superseding row, by the model's own docstring, is supposed to
**repeat that exact triple**:

> "A superseding row replaces the *same* (sample, element, digest) triple;
> it does not create a third row."

The constraint and the design contradict each other, and the constraint
wins. Every call to `MultiElementService.supersede()` raises on flush:

```
psycopg.errors.UniqueViolation: duplicate key value violates unique
constraint "uq_multi_element_sample_element_digest"
DETAIL:  Key (sample_id, element, digest_method)=(6186, Cu, aqua_regia)
         already exists.
```

`supersede()` has never worked. Compare `fire_assay_result` — the table
this one was explicitly modelled on — which carries **no unique
constraint at all**, and instead prevents a branching chain in the
*service* layer (see `test_fire_assay_results_service.py:314`, "Prevents a
branching chain: only the head can be corrected"). Phase 4 added a
database constraint the pattern it copied never had, and it broke the
pattern.

### F2 (high) — A second digest silently reverts a reported sample

**Where:** `multi_element/service.py:138–176`

```python
existing = current_results(self._session, sample.id)
if not existing:
    check_transition(source=sample.status, target=SampleStatus.ASSAYED, ...)
# ... rows written ...
sample.status = SampleStatus.ASSAYED   # unconditional
```

The lifecycle gate only runs when the sample has **no** existing element
results. A sample that has already been certified and reported *has*
element results, so `existing` is non-empty, the gate is skipped — and the
last line still fires. A four-acid digest arriving after the aqua-regia
certificate walks a terminal `REPORTED` sample backwards. Reproduced
directly: `status after second digest = SampleStatus.ASSAYED`.

This is exactly the class of bug Phase 3 exists to eliminate. PROGRESS.md
states "every sample-status move in the spine now goes through
`check_transition` for real" — that stopped being true the moment this
commit landed.

### F3 (high) — A correction is timestamped with the analyst's signup date

**Where:** `multi_element/service.py:213`

```python
analysed_at=analyst.created_at or datetime.now().astimezone(),
```

`supersede()` takes no `analysed_at` parameter, so it reaches for the
analyst's own row. `LabUser.created_at` is non-null in practice, so every
corrected reading claims to have been analysed the moment that user's
*account* was created — a date that can precede the sample's own receipt.
This value flows into the certificate, the provenance dossier, and the
dossier's sealed hash. The fallback (`datetime.now()`) is equally wrong —
that is when the correction was *typed*, not when the instrument read.
Compare `fire_assay_results`, where `analysed_at` is a required input on
every path, with no fallback of any kind.

### F4 (high) — `element_grade` is dead code; the write path bypasses the domain

**Where:** `domain/assay.py:224`, `multi_element/service.py:283`

This commit adds a careful 90-line domain function: it refuses a
mass-fraction unit where a concentration belongs, refuses a non-fraction
output unit, returns a non-detect below the detection limit, and refuses a
bare zero. It is good work. **Nothing outside its own tests calls it:**

```bash
$ grep -rn "element_grade" --include='*.py' src tests
# → 14 hits, all in tests/unit/test_multi_element.py
# → 0 hits in any service, route, or model
```

The service instead accepts a caller-supplied `Decimal` plus free-text
unit, validated against a hardcoded tuple:

```python
if item.grade_unit not in ("ppm", "ppb", "g/t", "%"):
```

That tuple shadows `domain/units.py`'s `Unit` enum — 11 units, exact
rational conversion factors, pinned precision — and the value is stored in
a plain `String(16)`. Nothing keeps the tuple and the enum in sync, and no
conversion is ever possible on a stored row. The unit discipline that is
this codebase's whole reason for existing is decorative on this path.

### F5 (high) — The certificate prints below-detection grades as real numbers

**Where:** `certificates/service.py:83–91`

`MeasuredValue` — the censored-value type with no `__float__`, this
project's signature commitment — is bypassed on the one path that issues
the signed document:

```python
def _display_element_grade(mer: MultiElementResult) -> str:
    """Same rounding discipline as ``_display_grade`` but reads directly
    from the model row rather than reconstructing a MeasuredValue."""
    rounded = mer.grade_value.quantize(...)
    return f"{rounded} {mer.grade_unit}"
```

`detection_limit` is stored on the row and never consulted here. An
arsenic reading of `0.002 ppm` against a `0.010 ppm` detection limit is
certified as `0.002 ppm` — a detected value — where it must read
`<0.010 ppm`. There is no `grade_censored` column on
`multi_element_result` at all, though `fire_assay_result` has `au_censored`
and this very commit added `silver_censored` right next to it.

Secondary defect in the same function: `_CERTIFICATE_GRADE_PRECISION` is
documented as calibrated for a fire-assay g/t number. Applied to a ppb
reading it rounds to three decimals of the wrong scale.

### F6 (high) — The service and both routes have zero tests

**Where:** `tests/integration/`, `tests/unit/test_multi_element.py`

Every other service in this repo has a matched pair —
`test_x_service.py` and `test_x_api.py`. There is no
`test_multi_element_service.py` and no `test_multi_element_api.py`.
Nothing under `tests/` imports `MultiElementService`. The 158-line
`test_multi_element.py` that does exist is good, and tests only the pure
function nothing calls (F4). This is the root cause of F1–F3 all reaching
`main`.

### F7 (medium) — The element-read endpoint reopens a closed hole

**Where:** `web/routes/multi_element.py:84`

The post-Phase-1 hardening put every grade-bearing read behind
`InternalActorDep` — samples, certificates, batches, audit, flux recipes,
QC materials — specifically so the external `client` role cannot read any
grade by id. `GET /api/samples/{id}/multi-element-results` uses plain
`ActorDep`, reopening exactly that hole for element data. One-word fix.

### F8 (medium) — CI's lint gate fails on the committed tree

7 files need `ruff format`. `make fmt` clears it. Fix this first so every
later check is trustworthy.

### F9 (medium) — A certificate's digest label is last-write-wins

**Where:** `certificates/service.py:139`

```python
digest_by_sample[mer.sample_id] = mer.digest_method.value
```

The design explicitly allows two digests per sample. Collapsing them into
one scalar means a certificate covering both labels the whole sample with
whichever row sorted last. The per-element `digest_method` on
`CertifiedElementInfo` is correct; the summary scalar is the one that
lies.

### F10 (medium) — Certificate issuance is N+1

**Where:** `certificates/service.py:165` — `current_results` runs once per
sample inside the issuance loop. Not urgent at demo scale; noted because
`list_clients`, added in this same commit, already does the better thing
(one grouped query).

### F11 (medium) — Function-local imports and the repo's only `type: ignore`

`web/routes/multi_element.py:47,91,98` and `certificates/service.py:168`
import inside function bodies for no structural reason (no circular
import forces it — all four hoist cleanly to module scope). Separately,
`_to_domain` carries `# type: ignore[no-untyped-def]`, the only
suppression anywhere in `src/`; a `-> ElementResult` return annotation
removes it.

---

## Part II — Track A: close Phase 4

**Goal:** the multi-element slice reaches the standard Phases 1–3 already
hold — domain-wired, lifecycle-correct, tested in matched pairs, walked
live end to end. **Do these in order** — A0 makes the gate trustworthy
before you rely on it, A1 unblocks every test you'll write for the
supersession path, and A4 changes a type that A5 and A7 both build on.
Cut one commit per item.

### A0 — Make the gate green (fixes F8, F11) — ~10 min

```bash
make fmt
```

Then hoist the four function-local imports in
`web/routes/multi_element.py` and `certificates/service.py` to the top of
their files. Add `-> ElementResult` to `_to_domain`'s signature and delete
its `type: ignore[no-untyped-def]`. Run `make check` and confirm it is
clean. **Commit this alone** — it is the baseline every later commit in
this track is measured against.

### A1 — Replace the unique constraint (fixes F1) — ~45 min

Don't just drop the constraint and rely on the service layer the way
`fire_assay_result` does — express the real invariant in the database:
**a row may be superseded at most once.** That's a partial unique index
on `supersedes_id`, and it makes a branching chain structurally
impossible rather than merely refused in Python.

```bash
make revision m="multi element supersession chain"
```

```python
def upgrade() -> None:
    op.drop_constraint(
        "uq_multi_element_sample_element_digest",
        "multi_element_result",
        type_="unique",
    )
    op.create_index(
        "uq_mer_one_successor_per_row",
        "multi_element_result",
        ["supersedes_id"],
        unique=True,
        postgresql_where=sa.text("supersedes_id IS NOT NULL"),
    )

def downgrade() -> None:
    op.drop_index("uq_mer_one_successor_per_row", table_name="multi_element_result")
    op.create_unique_constraint(
        "uq_multi_element_sample_element_digest",
        "multi_element_result",
        ["sample_id", "element", "digest_method"],
    )
```

Note in the migration docstring that `downgrade()` restores a constraint
that cannot hold once any correction exists in the data — true from
empty, which is all CI's downgrade check verifies, but worth stating so
nobody is surprised running it against real data later.

In `db/models.py`, delete the `UniqueConstraint` from
`MultiElementResult.__table_args__` and add the matching
`Index(..., unique=True, postgresql_where=...)` so `alembic revision
--autogenerate` doesn't propose reverting your migration on the next run.

**Once this lands, consider a follow-up ticket** (not part of this track)
backporting the same partial index to `fire_assay_result`, which has the
identical invariant and currently defends it only in Python.

### A2 — Gate every status move, not just the first (fixes F2) — ~20 min

Replace the emptiness check with a check on the sample's actual status,
and move the assignment inside the guard:

```python
# before
existing = current_results(self._session, sample.id)
if not existing:
    check_transition(...)
# ... write rows ...
sample.status = SampleStatus.ASSAYED

# after
if sample.status is not SampleStatus.ASSAYED:
    check_transition(
        source=sample.status,
        target=SampleStatus.ASSAYED,
        sample_type=sample.sample_type,
        role=actor_role,
    )
    sample.status = SampleStatus.ASSAYED
# ... write rows ...
```

Three behaviours fall out for free: a `RECEIVED` sample is still refused;
an already-`ASSAYED` sample importing a second digest is a no-op on
status; a `REPORTED` sample is refused by `check_transition` itself,
naming the amended-certificate remedy, because the state machine already
knows `REPORTED` is terminal. Keep the guard **above** the row writes so a
refusal costs nothing.

### A3 — Make `analysed_at` a required correction input (fixes F3) — ~20 min

Add `analysed_at: datetime` and `method_notes: str | None = None` as
keyword parameters on `MultiElementService.supersede()`. Delete the
`analyst.created_at or datetime.now()` fallback; pass both parameters
through to the new row instead.

While here, add the route this method never got — that's a large part of
why F1 stayed hidden: a correction path that only exists as an untested
Python method:

```python
PATCH /api/samples/{sample_id}/multi-element-results/{element}
```

Body: `{digest_method, grade_value, grade_unit, detection_limit,
analysed_at, reason}`.

### A4 — Wire the write path through `Unit` (fixes F4) — ~2–3 hours

Two steps. Do step 1 always; do step 2 if you have time in this pass —
it's the bigger lift but it's what actually retires F4.

**Step 1 — type the unit.** Change `ElementResult.grade_unit` from `str`
to `Unit`. In `_validate_import`, replace the hardcoded tuple:

```python
# before
if item.grade_unit not in ("ppm", "ppb", "g/t", "%"):
    problems.append(f"element {item.element.value}: unrecognised unit {item.grade_unit!r}")

# after
if dimension_of(item.grade_unit) is not Dimension.MASS_FRACTION:
    problems.append(
        f"element {item.element.value}: {item.grade_unit.value} measures "
        f"{dimension_of(item.grade_unit).value}, but a grade is a mass fraction"
    )
```

Parse `Unit(schema.grade_unit)` at the route/schema boundary, so a bad
string becomes a 422 from Pydantic rather than a service-layer string
comparison. The database column stays `String(16)` storing `unit.value` —
the same pattern `fire_assay_result.au_unit` already uses.

**Step 2 — accept what the instrument actually produces.** Today the
analyst must hand-compute ppm before entry — exactly the calculation
`element_grade` exists to own. Add a second entry point, mirroring the
`create` / `solution_finish` split `fire_assay_results` already has:

```
POST /api/samples/{id}/multi-element-results/raw
{
  "digest_method": "four_acid",
  "solution_volume_ml": "100",
  "sample_weight_g": "0.5",
  "analysed_at": "2026-08-28T09:00:00Z",
  "readings": [
    {"element": "Cu", "concentration": "0.7125",
     "concentration_unit": "mg/L", "output_unit": "ppm",
     "detection_limit": "0.005"}
  ]
}
```

The service calls `element_grade(...)` once per reading and stores the
returned `MeasuredValue`. Keep the existing bulk-import endpoint — a lab
importing a vendor's already-reduced CSV genuinely has only final grades
— but the raw path is the one the domain function was built to serve.

### A5 — Carry censoring to the certificate (fixes F5) — ~1 hour

Migration: add `grade_censored BOOLEAN NOT NULL DEFAULT false` to
`multi_element_result`. Existing append-only grants already cover new
columns on this table — no companion grants migration needed (see
`c3d4e5f6a7b8`'s docstring for the precedent).

In the service, on whichever path wrote the row: when a detection limit
is present and the value is at or below it, store `grade_censored=True`
with `grade_value` holding the limit. If you did A4 step 2, this is free
— `MeasuredValue.censored` already carries it.

Delete `_display_element_grade` and reconstruct a real value at the
render site instead:

```python
measured = (
    MeasuredValue.non_detect(mer.grade_value, Unit(mer.grade_unit))
    if mer.grade_censored
    else MeasuredValue.detected(mer.grade_value, Unit(mer.grade_unit), mer.detection_limit)
)
grade_display = _display_grade(measured)
```

One display function, one rounding rule, censoring honoured on the signed
document. While you're in `_display_grade`, make the rounding quantum
unit-aware — three decimals suits g/t and ppm, ppb wants zero, percent
wants two — or trace elements round to noise.

### A6 — Gate the element read (fixes F7) — ~5 min

```python
# web/routes/multi_element.py
def list_multi_element_results(
    sample_id: int,
    session: SessionDep,
    actor: InternalActorDep,   # was: ActorDep
) -> list[MultiElementResultOut]:
```

### A7 — Write the two missing test files (fixes F6, pins A1–A6) — ~3 hours

Follow the existing pair convention exactly — copy fixtures
(`analyst`, `supervisor`, `a_sample`) from
`test_fire_assay_results_service.py`.

**`tests/integration/test_multi_element_service.py`:**

- `test_a_corrected_reading_supersedes_the_old_one` — the F1 regression;
  must reach a real flush without raising
- `test_a_chain_cannot_branch` — superseding a non-head row is refused by
  the new partial index
- `test_current_results_returns_one_head_per_element_and_digest`
- `test_a_second_digest_does_not_revert_a_reported_sample` — the F2
  regression
- `test_a_received_sample_is_refused`
- `test_a_rejected_sample_is_refused`
- `test_the_stored_analysed_at_is_the_one_the_caller_gave` — the F3
  regression
- `test_a_reading_below_its_detection_limit_is_stored_censored`
- `test_a_concentration_unit_is_refused_as_a_grade_unit`
- `test_a_duplicate_element_refuses_the_whole_import` — and assert
  **zero rows were written**, proving the all-or-nothing claim in the
  module docstring
- `test_the_restricted_role_cannot_update_or_delete` — the same
  append-only proof `test_append_only.py` runs for every other table

**`tests/integration/test_multi_element_api.py`:**

- 201 on a valid bulk import
- 422 when the URL's `sample_id` disagrees with the body's
- 409 on an import against a sample in the wrong lifecycle state
- 403 for the `client` role, on both the read and the write endpoint
- a round trip: import, then GET, and assert the elements come back
  correctly

### A8 — Walk it live, then update the record — ~1 hour

```bash
make migrate && make seed && make run
```

Drive the full path with curl, the way Phases 1–3 each did: register a
client → submit a sample → prep it → charge a crucible → assay it → import
an ICP run → correct one element → issue a certificate → confirm the PDF
shows `<DL` for the censored element → fetch the sample's provenance
dossier and recompute its seal offline. Paste the transcript into
PROGRESS.md's "Verified live" section, the way every earlier phase's
transcript is recorded there.

Then:

```bash
make generate-types   # A3/A4/A5 changed request/response schemas
make verify-chain
```

Finish with a PROGRESS.md entry recording F1–F11 and how each was
resolved, in the same audit-and-hardening style the file already uses for
the post-Phase-1 pass.

---

## Part III — Track B: Phase 5, the Sentinel seam

Do not start this until Track A is closed — B1's golden-file discipline
below is only meaningful once the element data it exports is correct.

**The load-bearing constraint, restated:** this integration requires
**zero** changes to QC Sentinel. Sentinel's own claims — append-only by
grant, no write-backs ever, advisory disposition — mean something only
because it sits *beside* the system of record. If this seam ever seems to
need a new Sentinel endpoint, that's a sign the seam is drawn wrong, not a
reason to add the endpoint.

### B1 — The export format, as a pure function — ~3 hours

New module `src/msa_lims/sentinel/export.py`. Pure — no session, no
clock, no HTTP, matching the rule every other file in `domain/` follows.

```python
def to_generic_csv_v1(rows: Sequence[QcRow]) -> str: ...
def to_wide_icp_v1(rows: Sequence[QcRow]) -> str: ...
```

`QcRow` is a frozen dataclass the caller assembles from ORM data — it
deliberately does not take ORM models itself, so the format is testable
without a database. The input is the QC material insertions already
assembled by `qc_dossiers/service.py`; that module did the hard part
during Phase 4's sealed-dossier work.

**Build this against golden files, not against memory of Sentinel's
parser.** Copy two or three real fixture CSVs out of
`~/IdeaProjects/fireAssay`'s own parser tests into
`tests/fixtures/sentinel/` in this repo, and assert byte-level agreement
on headers and value formatting. That is the only thing keeping the two
repos honest without coupling them at the code level.

### B2 — Record what was sent, before sending it — ~2 hours

New migration for `sentinel_submission` — append-only, explicit grants,
no `ALTER DEFAULT PRIVILEGES`, following the Phase 0 rule:

```
id, batch_id FK, format TEXT, payload_sha256 TEXT,
sentinel_import_id TEXT NULL, submitted_at TIMESTAMPTZ,
submitted_by FK lab_user, http_status INT NULL,
last_polled_at TIMESTAMPTZ NULL, verdict JSONB NULL
```

Hash the exact bytes posted, so the LIMS can always answer "what did we
send Sentinel?" without asking Sentinel. Reuse `domain/canonical.py` — it
already exists for the provenance seal. Write through
`record_audit_event`, the sole write path the hash chain depends on.

### B3 — The client, with failure modes named — ~3 hours

`src/msa_lims/sentinel/client.py` — `httpx`, a timeout, bounded retry on
5xx and connection errors only. Config keys: `MSA_SENTINEL_BASE_URL`,
`MSA_SENTINEL_TOKEN`, `MSA_SENTINEL_ENABLED` (default `false`, so the
test suite and a fresh clone never reach the network by accident).

Distinguish three states in the row, because an operator will ask: never
submitted, submitted-and-unacknowledged, submitted-and-verdicted. A
failed POST must still leave the `sentinel_submission` row behind with
its `http_status` — the attempt itself is a fact worth keeping.

### B4 — Poll verdicts, store them advisory, never write back — ~2 hours

```
POST /api/batches/{id}/submit-to-sentinel
GET  /api/batches/{id}/sentinel-verdict
```

The verdict lands in `sentinel_submission.verdict` as JSONB and is read
back as-is. **Nothing in this path may touch `fire_assay_result`,
`multi_element_result`, or `sample.status`.** Add a test asserting exactly
that. The boundary between the two systems is the entire reason they are
separate repos, and a test is the only thing stopping a future
"convenience" from crossing it.

### B5 — Show it as advice, in the UI's own voice — ~2 hours

A verdict panel on `BatchDetail.tsx`, below the furnace tray. Label it as
Sentinel's opinion, timestamp the poll, and give a failed or
never-reached Sentinel a visibly distinct state from a clean verdict — an
operator must never be able to read "no problems reported" off a request
that never actually arrived. Run `make generate-types` after the schema
changes.

---

## Part IV — Track C: Phase 6 and the standing questions

PROGRESS.md's open-questions list is the best planning asset this project
has — it's already honest about what's missing. These are the items whose
time has come, ranked by how much each one unlocks. **Pick from this list;
don't work through it in order** — none of it blocks anything else here.

| Item | Why now | Effort |
|---|---|---|
| **C1 · Instrument registry** — register balances, furnaces, ICP units with calibration data | Closes four open questions at once: balance sensitivity stops being a per-request guess, the solution finish's detection limit gets a real home, `fire_assay_result` gains a real `instrument_id` for contamination tracing, and `Batch` can finally carry per-furnace tray geometry instead of one lab-wide grid. **Do this one first if you only do one.** | ~1 wk |
| **C2 · Certificate staleness** — flag certificates whose certified results were later superseded | The amendment path already works and is tested; nothing surfaces the *need* for one. A single anti-join view plus a badge on the certificate read turns a manual noticing into a system property. | ~2 d |
| **C3 · Chain-tip concurrency** — `SELECT … FOR UPDATE` on the audit tip | The hash chain assumes one writer. Two concurrent requests can both read the same tip and both write a row claiming to follow it, branching the chain. Small, local fix in `db/audit.py`. | ~1 d |
| **C4 · Typed error responses** — real `responses={409: …, 422: …}` per route | Graduates Schemathesis's `response_schema_conformance` and `positive_data_acceptance` checks from "deliberately excluded" to enforced. | ~3 d |
| **C5 · Frontend auth** — login, token storage, real headers | The React app sends no auth headers today and rides `dev_headers`'s least-privileged default. Nothing role-gated can be demonstrated through the UI, including certificate signing — the most compelling thing this system does. | ~1 wk |
| **C6 · OpenTimestamps anchor** — periodic external anchoring of the chain head | The named remaining half of audit idea #1. Internal consistency is proven; *when* the head existed is not. Do after C3. | ~3 d |
| **C7 · Pagination** — cursor on `GET /api/samples` | Only urgent once the seed dataset approaches the 500-row cap. Listed so it isn't forgotten when that happens. | ~1 d |

### What not to build yet

Two items on the open-questions list are worth actively declining for
now, not just deprioritising. **A client portal with row-scoped reads** —
the current `internal_actor` refusal is an honest posture; real scoping
needs a durable LabUser↔Client association plus per-endpoint scoping on
every read, which is a phase, not a task, and nothing currently needs it.
**An un-charge / cancel-batch correction path** — the right shape here is
genuinely unknown (delete the crucible? supersede it? reject and
re-charge?), and this codebase's habit of refusing half-finished features
rather than guessing is exactly why it reads as well as it does. Wait for
a real workflow to force the answer.

---

## Definition of done, for every item above

Before checking anything off this document, all four must be true —
this is the standard Phases 0–3 held and Phase 4 didn't:

1. `make check` (lint + mypy strict + full test suite) is clean.
2. A matched pair of tests exists — `test_x_service.py` and
   `test_x_api.py` — covering the happy path, the refusal paths, and (for
   anything append-only) a direct proof the restricted role cannot
   `UPDATE`/`DELETE`.
3. The feature has been driven live with curl (or through the UI) against
   a freshly migrated database, and the transcript is in PROGRESS.md.
4. If any request/response schema changed, `make generate-types` was run
   and its output committed.

If a work item can't clear all four in one sitting, split it rather than
merging it half-done — that's the discipline this codebase already
expects of itself.
