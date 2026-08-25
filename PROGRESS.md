# MSA LIMS — Progress

**Updated:** 2026-08-25 · **Phase:** 2 in progress — furnace batching (batches, crucibles, flux recipes) is built and verified live; QC insertion policy and wiring a result to the crucible it came from remain

New to the codebase? Read [docs/ENGINEERING_GUIDE.md](docs/ENGINEERING_GUIDE.md)
first — it explains the decisions this document only tracks.

---

## Status at a glance

| Phase | Window | State |
|---|---|---|
| 0 · Skeleton | wk 1 | **Done** — domain core, schema, grants, CI gate, UI shell |
| 1 · The spine | wk 2–4 | **Done** — auth, all reference-data registration, submission intake, fire assay result entry, Certificate of Analysis issuance, sample/certificate lookup, and the sample list/detail React screens, all built and verified live |
| 2 · Fire assay batching | wk 5–7 | **In progress** — batches, crucibles (position-constrained), and flux recipes (matrix-scaled) built and verified live; QC insertion policy and crucible↔result wiring still open |
| 3 · Lifecycle & prep | wk 8–9 | Not started |
| 4 · ICP & bulk import | wk 10–11 | Not started |
| 5 · The Sentinel seam | wk 12–13 | Not started |
| 6 · Ship the story | wk 14–16 | Not started |

**Health:** 377 tests passing · ruff clean · mypy `--strict` clean · migrations
apply from empty and are reversible (including the two new furnace-batching
migrations, checked with a full `downgrade base` / `upgrade head` round trip)
· frontend builds and typechecks clean (TypeScript `strict` +
`noUncheckedIndexedAccess`) · verified live through curl end to end: register
a client, submission, and flux recipe; open a batch; charge a crucible with
scaled reagent amounts; fire the batch through every furnace stage to
`completed`, watching crucible status bulk-advance; and enter a fire assay
result against the now-`in_assay` sample, proving Phase 1 and Phase 2 compose
without a seam. **Phase 1 — the thinnest complete thread the original roadmap
called for — is done. Phase 2's core batching flow is done; QC insertion
policy and crucible↔result wiring remain.**

---

## What exists now

### Foundations
- Python 3.11 venv, `pyproject.toml`, ruff + mypy strict + pytest + hypothesis.
- `docker compose`: Postgres 16 on **port 5435**. 5432–5434 were already taken
  on this machine (interactive-planner, francais, QC Sentinel).
- `Makefile` targets: `install`, `services`, `test`, `test-unit`, `check`,
  `migrate`, `seed`, `revision`, `run`, `ui`.
- GitHub Actions gate running lint, typecheck, migrations-from-empty, and the
  full suite against a real Postgres service.

### Domain core — `src/msa_lims/domain/` (pure, no I/O)
- **`units.py`** — 11 units across 3 dimensions, exact rational conversion
  factors, precision pinned so results never depend on ambient decimal context.
  Ported from QC Sentinel and **extended with a mass dimension**, because a LIMS
  weighs things and Sentinel only ever compared concentrations. Cross-dimension
  conversion is refused: turning milligrams into g/t needs the sample weight, so
  it is a calculation with a named input, not a unit conversion.
- **`values.py`** — `MeasuredValue`, the censored-value type. No `__float__`;
  callers must use `require_detected()` or name a `Substitution`. Ported
  unchanged in substance — the argument for it is stronger here than in
  Sentinel, because this system issues the certificate.
- **`assay.py`** — the gravimetric grade calculation. Doré and parted-gold bead
  weights are separate named parameters; silver by difference refuses transposed
  weights rather than reporting a negative grade; a bead below balance
  sensitivity returns a **non-detect at the corresponding grade**, not a very
  small number. The assay ton is held as the exact rational 175/6 and a test
  asserts the identity it exists for: 1 mg from one assay ton is exactly
  1 oz/t.
- **`sample_id.py`** — drill and surface label parsing, and `DepthInterval` with
  a **half-open** convention so contiguous sampling never reads as an overlap.
  `find_overlaps` reports every conflicting pair, not the first. Gained
  `format_hole_id` / `canonical_hole_id`: the one function both
  `SampleIdentity.hole_id` (computed from a parsed sample label) and drill-hole
  registration now go through, so a hole registered as `"msa-24-001"` and a
  drill sample labelled `"MSA-24-001-…"` provably resolve to the same string
  rather than coincidentally agreeing.
- **`lifecycle.py`** — the sample state machine. Pure. Pulps skip preparation;
  `Reported` is terminal; rejection after a result exists is refused with a
  message naming the real remedy (an amended certificate).
- **`enums.py`** — closed vocabularies, with the two authorisation tiers.

### Authentication — `src/msa_lims/auth/`, `src/msa_lims/web/deps.py`
- **`oidc.py`** — bearer-token verification against a provider's published
  keys, ported line-for-line from Sentinel's module with the role vocabulary
  swapped. **No configuration flag turns signature verification off** — a test
  reads the module's own source and asserts the string does not appear. Roles
  are mapped from OIDC groups explicitly; an unmapped group grants nothing, with
  no fallback to analyst.
- **Two modes, chosen by `MSA_AUTH_MODE`.** `dev_headers` trusts `X-Actor` /
  `X-Actor-Role` and is refused with **501** outside `local`/`ci` — even with
  the shim itself compromised, it cannot silently work in a real deployment.
  `oidc` verifies a real bearer token; a missing or malformed one is **401**
  ("log in again"), a valid token with no mapped role is **403** ("ask an
  administrator") — the two are kept apart because they send the caller to
  different places.
- **`CLIENT` sits at the bottom of the privilege order**, below every internal
  role. A person in both the `clients` group and an internal one at the
  provider must never be resolved to `CLIENT` by the "most privileged group
  wins" rule — the ordering in `_PRIVILEGE` enforces that directly, and a test
  proves it.
- `GET /api/me` — the cheapest possible thing that exercises `ActorDep`, so a
  developer or a curl script can find out who the system thinks they are before
  trusting any write path built on the same dependency.
- **`current_lab_user`** (`web/deps.py`) resolves the open "`Actor` vs.
  `LabUser`" question from Phase 0: a `LabUser` row is looked up — or
  provisioned on first sight — keyed on `Actor.subject`, never on name or
  email, both of which people change. `LabUser.role` is kept in sync as a
  courtesy for joins; **no authorisation check ever reads it** — every check
  reads `Actor.role` fresh from the current request, so a stored role can never
  outlive what the identity provider currently grants.

### Submission intake — `src/msa_lims/submissions/`
- **`service.py`** — the first write path, and the first thing to exercise a
  `domain/lifecycle.py` role check through HTTP. Only `BENCH_ROLES` may
  receive a submission; `CLIENT` is refused with **403**.
- **Validates the whole batch before writing anything.** Unreadable labels,
  duplicate labels within the batch, labels already received in an earlier
  submission, drill samples naming no project, drill samples against an
  unregistered hole, and overlapping depth intervals (checked against both the
  new batch and everything already on record for that hole) are all collected
  and reported together — mirrors Sentinel's ingestion validation and
  `domain.sample_id.find_overlaps`, both of which report every problem in one
  pass rather than failing on the first.
- **Unregistered drill holes are refused, never invented.** A drill sample
  references its hole by the label's parsed `hole_id`; if no `DrillHole` row
  matches under the named project, the sample is rejected with the remedy
  named in the message, rather than the service silently creating a hole with
  null coordinates (mirrors Sentinel's FR-5: unregistered reference data
  quarantines, it is never auto-created).
- **Submission numbering is provisional and says so in its own docstring** —
  `SUB-2026-0841`-shaped, correct only under the single-writer assumption
  already documented for this schema (see `db/base.py`'s `IdPk` comment), not
  safe against two concurrent front-desk submissions racing for the same
  number. The real convention is still an open question.
- One `AuditEvent` per row created — one for the submission, one per sample —
  not one summarising event for the whole batch, matching `table_name`/
  `record_id` being a per-record grain everywhere else in the schema.

### Client and project registration — `src/msa_lims/clients/`
- **`service.py`** — `POST /api/clients` and `POST /api/projects`, thinner than
  submission intake (no label parsing, no interval checking) but the same two
  disciplines: check every constraint before writing anything, and audit every
  row created. Restricted to `MAY_MANAGE_ACCOUNTS` (`supervisor`, `lab_manager`)
  — a new tier in `domain/enums.py`, deliberately narrower than `BENCH_ROLES`:
  a prep technician or analyst works material through the lab but does not set
  up billing relationships or drilling programs.
- **Uniqueness is checked before insert, not left to a raw `IntegrityError`.**
  Client `code` and `name` are each globally unique; a project `name` is unique
  within its client, so the same program name is fine for two different
  clients. All three are checked with a `SELECT` before the `INSERT`, so a
  re-submitted form comes back with a clear reason at 422 instead of an
  unhandled 500.
- **`ClientNotFoundError` was hoisted out of `submissions/service.py`** into
  this module — both features ask "does this client exist?", and a caller
  catching one must not be able to miss the other because two unrelated
  classes happened to share a name. `submissions/service.py` now imports and
  re-exports it, so nothing downstream had to change.
- Flat request bodies, not nested REST (`POST /api/projects` with `client_id`
  in the body, not `/api/clients/{id}/projects`) — matches how
  `SubmissionCreate` already names its own parent, so there is one convention
  for "how does a resource point at its parent" rather than two that disagree.

### Drill hole registration — `src/msa_lims/drill_holes/`
- **`service.py`** — `POST /api/drill-holes`, the last piece of reference data
  submission intake depends on. Restricted to `BENCH_ROLES`, not
  `MAY_MANAGE_ACCOUNTS`: a hole's collar coordinates and depth typically arrive
  with the drill log accompanying a core shipment — the same moment a
  submission is received — rather than as a business decision about who the
  lab works for.
- **Canonicalises `hole_id` through the same function submission intake's
  lookup relies on.** A hole registered as `"msa-24-001"` is stored as
  `"MSA-24-001"`, and a drill sample's parsed `hole_id` renders through the
  identical `format_hole_id` — see `domain/sample_id.py` above — so the two
  can never spell the same hole two different ways. A test proves this
  directly: a hole registered in one case is the exact row a submission's
  drill sample in another case resolves to, and the live verification below
  exercised the same path through real HTTP.
- Duplicate holes within a project are refused after the same canonicalisation
  — `"msa-24-001"` collides with an already-registered `"MSA-24-001"` — and
  `dip_degrees`/`azimuth_degrees`/`total_depth_m` are range-checked at the
  Pydantic layer to mirror the database's own CHECK constraints, so a bad
  value comes back as a clean 422 rather than a raw `IntegrityError`.
- `ProjectNotFoundError` was added to `clients/service.py` — not
  `drill_holes/service.py` — for the same reason `ClientNotFoundError` lives
  there: the module that owns an entity owns the "this one doesn't exist"
  error for it, so every caller asking the same question gets the same answer
  type.

### Fire assay result entry — `src/msa_lims/fire_assay_results/`
- **`service.py`** — `POST /api/fire-assay-results`, the first write path
  that *computes* something rather than only storing what it was told. Calls
  `domain.assay.gravimetric_grade` directly on the raw weighing, so the number
  a future certificate reports is reproducible from a bead weight and a
  sample weight years later, not asserted. Restricted to `MAY_ENTER_RESULTS`
  (`analyst`, `supervisor`, `lab_manager`) — a `prep_tech` weighs and
  pulverises material but does not enter or interpret a result, so the tier
  deliberately excludes it even though it's inside `BENCH_ROLES`.
- **Scope note, stated in the module's own docstring:** only
  `FIRE_ASSAY_GRAVIMETRIC` is entered here. AAS and ICP-MS read a
  concentration off a calibration curve, not a bead weight, and need an
  entirely different input shape — so there is no method parameter on the
  request and no dead validation branch pretending to support a method
  nothing here implements. `AssayMethod` on the stored row still carries the
  full vocabulary, because AAS/ICP-MS are real methods this schema already
  named in Phase 0, even though nothing writes them yet.
- **`fire_assay_result` is genuinely append-only**, not append-only by
  convention — `msa_app` holds SELECT/INSERT only on it (new migration
  `9a1c2e6f4b3d`, following `b1d0c4e77a10`'s own comment: *"As results,
  certificates and their amendments arrive in later phases they join this
  tuple, not the one above"*). A test proves it directly against the real
  role — UPDATE and DELETE both refused — and the live verification below
  proved the identical thing with `psql` and the application's own
  credentials.
- **A correction is a new row, never an `UPDATE`.** `supersedes_id` points at
  the result it corrects; `superseded_reason` is required and enforced by a
  CHECK, mirroring `audit_event`'s own amendment-reason constraint. "Current"
  is computed by exclusion — the row nothing else's `supersedes_id` names —
  rather than a stored flag, so there is nothing to keep in sync when a
  correction lands. Entering a brand-new result against a sample that already
  has a current one is refused ("supersede it explicitly"); superseding a row
  that is not currently the head of the chain is also refused, so a chain
  cannot branch — the same rule QC Sentinel enforces on its re-assay chains.
- **Entering a sample's first result moves it straight to `ASSAYED`,
  skipping every intermediate status.** Stated plainly in the module
  docstring as a known Phase 1 simplification, not routed through
  `domain/lifecycle.py`'s `Transition` table (which would require the sample
  to already be `IN_ASSAY`, unreachable until furnace batching exists).
  Superseding a result does **not** touch sample status — correcting a
  number and physically re-running a sample through the furnace are
  different acts, and the latter is `domain/lifecycle.py`'s separate,
  already-modelled `ASSAYED → READY_FOR_ASSAY` re-assay transition.
- The response's `au` field is shaped to match `frontend/src/types.ts`'s
  `MeasuredValue` exactly (`value`, `detection_limit`, `censored`, `unit`),
  so the existing `formatMeasured` helper renders a non-detect correctly the
  moment a screen exists to show one.
- `current_result`/`measured_value` were promoted from module-private to
  exported — `certificates/service.py` needs the identical "what is the
  current result for this sample" question answered the identical way, and
  the identical `MeasuredValue`-from-columns reconstruction, so a certificate
  can never disagree with the API about what a sample's grade is.

### Certificate of Analysis — `src/msa_lims/certificates/`
- **`pdf.py`** — pure PDF rendering (no session, no clock; every fact printed
  arrives as data), and **byte-deterministic** — verified directly by a unit
  test that renders identical content twice and asserts identical bytes, not
  assumed. Two things make it true: `reportlab.pdfgen.canvas.Canvas(...,
  invariant=1)` disables the library's default of stamping an effectively-
  random document ID into the trailer on every render; and only the PDF
  spec's own standard 14 fonts are used, so nothing is embedded and nothing
  about font substitution can vary by platform.
- **`service.py`** — `POST /api/certificates`, restricted to
  `MAY_SIGN_CERTIFICATE` (`lab_manager` only — defined back in Phase 0,
  anticipating exactly this). A certificate covers an explicit list of
  `sample_ids`, not an implicit "whole submission": real certificates
  sometimes span multiple submissions or report a subset as results become
  available, and tying the model to "one submission" would have been the
  wrong shape for that.
- **Every sample must have a current fire assay result, or the whole request
  is refused** — collected into one list, same "report every problem"
  pattern as submission intake. A certificate cannot state a result nothing
  measured.
- **`certificate` is append-only, `certificate_result` is append-only** — new
  migration `2d5f8a17c930`, following through on `b1d0c4e77a10`'s own
  comment. `certificate_result` freezes the *specific* `fire_assay_result`
  row each sample certified at the moment of issuance — not just the sample
  — so if that result is later superseded, the certificate still records
  exactly what it actually reported. A certificate is a historical
  statement, not a live query.
- **Unlike `fire_assay_result`, there is no "only one current document"
  rule.** A client can hold many independent certificates over time — one
  per batch reported. Supersession only prevents one specific chain from
  branching (the anti-branching rule fire assay results already established),
  never how many separate certificates a client may have.
- **Issuing a certificate is the first Phase 1 write path that actually goes
  through `domain.lifecycle.check_transition`'s real, already-modelled
  `ASSAYED → REPORTED` transition** — fire assay result entry's own docstring
  named this transition as unreachable at the time it was written, because
  nothing existed yet to reach it from. This is where it becomes reachable.
  Re-certifying an already-`REPORTED` sample (the amendment case) leaves its
  status untouched.
- **The PDF is stored inline (`pdf_bytes`, `pdf_sha256`), not in a dedicated
  content-addressed blob store** — an explicit Phase 1 simplification, not a
  design commitment (see the model docstring and the open questions below).
  `pdf_sha256` is a *real* content hash, re-verified on every read by
  `GET /api/certificates/{id}/pdf` — mirrors Sentinel's raw-export download,
  which re-verifies its own content hash for the identical reason: a client
  handed a silently altered certificate would have no way to know.
- **`GET /api/certificates/{id}/pdf` is this system's first `GET` endpoint.**
  Every write endpoint before it was POST-only. A signed document is the
  first thing worth fetching again later, which is what finally motivated
  building one.
- **A real defect, found and fixed during live verification, not left for
  later:** a grade whose bead-to-portion division does not terminate
  (0.160 mg over 30 g) was printing the full 34-digit `Decimal` division
  artifact straight onto the certificate — `5.333333333333333333333333333333333
  g/t`. `certificates/service.py`'s `_display_grade` now rounds to three
  decimal places with `ROUND_HALF_EVEN` (matching `domain/units.py`'s own
  documented rounding convention) *only at the point a human reads the
  number* — the stored `fire_assay_result.au_value` keeps full precision,
  correct for recalculation and audit.

### Sample and certificate lookup — `src/msa_lims/samples/`, `certificates/service.py`
- **`GET /api/samples/{id}`** and **`GET /api/certificates/{id}`** — the
  first `GET` endpoints for anything beyond a certificate's raw PDF. Every
  write endpoint before these could only be confirmed by reading its own
  POST response or querying the database directly, which every test still
  did until now.
- **`samples/service.py`'s `get_sample_detail`** assembles a sample, its
  *current* fire assay result (via the same `current_result` fire assay
  result entry already exports), and every certificate that names it — found
  by querying `certificate_result` directly, not by inferring from the
  sample's status. A `REPORTED` sample whose certificate was later
  superseded is still `REPORTED`; the certificate list is what actually
  answers "which documents mention this sample," and every certificate that
  ever certified it stays listed even after an amendment, not just the
  current one.
- **`SampleNotFoundError` was *not* hoisted to `samples/service.py`** despite
  matching the "the module that owns the entity owns the error" pattern
  `ClientNotFoundError` and `ProjectNotFoundError` established — it stays in
  `fire_assay_results/service.py`, and `samples/service.py` imports it from
  there. `samples/service.py` already depends on that module for
  `current_result`; hoisting the exception the other direction would have
  created an import cycle (`fire_assay_results` needing `samples` right back)
  for no benefit the existing one-way dependency doesn't already give.
- **`get_certified_samples` is one query, shared by both the `POST` response
  and the new `GET`** — added to `certificates/service.py` alongside a new
  `get_certificate` helper (`get_pdf` now calls it too, rather than
  duplicating the lookup). A live check confirmed the `POST` and `GET`
  responses for the same certificate are byte-for-byte identical JSON, not
  just similarly shaped.
- **`CertificateOut`'s shape was corrected, not just extended.** It
  previously carried a `sample_count` int that the route computed from
  `len(request.sample_ids)` — a number that was never actually verified
  against what got written. It's replaced with a real `samples` list read
  back from `certificate_result`, each entry carrying the sample label, the
  specific `fire_assay_result_id` frozen at issuance, and its grade. This is
  a breaking change to the response shape; acceptable now because nothing
  outside this repo's own tests consumed it yet — the kind of correction to
  make before something external depends on the wart, not after.
- **`GET /api/samples`** — the first listing endpoint anywhere in this
  system, added specifically because a real sample-list React screen had
  nothing to list without one (already anticipated in the previous phase's
  open questions). Deliberately lean: `list_samples` is one query joining
  through `submission`/`client` for display names, with **no per-row grade
  lookup** — a hundred samples would otherwise cost a hundred extra queries
  for a fact the detail screen already shows. Optional `client_id` and
  `status` filters exist on the service and the route today; no UI calls
  them with a value yet (see open questions).

### Fire assay batching — `src/msa_lims/domain/flux.py`, `domain/batch_lifecycle.py`, `flux_recipes/`, `batches/`
- **`domain/flux.py`** — pure Decimal scaling from a flux recipe's nominal
  charge to what a technician actually weighs out, mirroring `domain/assay.py`'s
  discipline exactly: a pinned `localcontext` precision, no session, no clock.
  Scaling is linear (doubling the sample weight doubles every reagent), which
  is the entire physical premise of a recipe — it specifies proportions, not
  absolutes.
- **`domain/batch_lifecycle.py`** — a second, independent state machine
  alongside `domain/lifecycle.py`'s sample one, for `BatchStatus`. **Strictly
  linear, no branch and no way back** — unlike a sample, a batch cannot be
  "returned" the way a re-assay returns a sample to `READY_FOR_ASSAY`, because
  a batch describes a furnace run that already physically happened. Reuses
  `domain.lifecycle`'s `TransitionNotAllowedError`/`InsufficientRoleError`
  rather than a parallel exception hierarchy: a batch refusal and a sample
  refusal mean the same two things, and a caller that already catches those
  types for one catches the other for free. Also owns `check_position`
  (furnace-tray bounds) and `bulk_crucible_status` (which two batch moves —
  reaching `FUSED` and `CUPELLED` — advance every charged crucible's status in
  lockstep, because a furnace fuses or cupels a whole tray at once).
- **`flux_recipes/service.py`** — `POST /api/flux-recipes`, thin like
  `clients/service.py`: one uniqueness check on `name`, one audit event.
  Restricted to a **new role tier, `MAY_CONFIGURE_LAB`** (supervisor,
  lab_manager) — the same two roles as `MAY_MANAGE_ACCOUNTS` today, but a
  deliberately separate constant: defining what goes into a furnace and
  setting up a client's billing relationship are different kinds of authority
  that happen to be held by the same people in this lab, the identical
  reasoning `MAY_MANAGE_ACCOUNTS`'s own docstring already gives for staying
  distinct from `BENCH_ROLES`.
- **`batches/service.py`** — `POST /api/batches` (open, starts `PENDING`),
  `POST /api/batches/{id}/crucibles` (charge), `PATCH /api/batches/{id}/status`
  (advance), `GET /api/batches/{id}` (detail) — built alongside the writes
  this time, not deferred the way Phase 1's `GET` endpoints were.
- **Charging bypasses `domain.lifecycle`'s `READY_FOR_ASSAY -> IN_ASSAY`
  transition, exactly the way `fire_assay_results/service.py` bypasses the
  lifecycle table for entering a result, and for the identical reason.**
  Investigation before writing any code confirmed `check_transition` is
  called from exactly one place in the whole codebase —
  `certificates/service.py`'s `ASSAYED -> REPORTED` move — meaning no sample
  can currently reach `READY_FOR_ASSAY` through the modelled path (prep-stage
  tracking doesn't exist yet). Routing crucible charging through
  `check_transition` honestly would only ever succeed for a sample that
  reached `READY_FOR_ASSAY` some other way that also doesn't exist. Instead, a
  sample is chargeable from any pre-assay status — everything except
  `IN_ASSAY`, `ASSAYED`, `REPORTED`, `REJECTED` — and charging moves it
  straight to `IN_ASSAY`, stated plainly in the module docstring as the
  honest reflection of what this system currently tracks.
- **A batch must be `CHARGING` before any crucible can be placed into it** —
  `PENDING -> CHARGING` ("open for charging") is a deliberate, separate step,
  mirroring the sample lifecycle's "start preparation," rather than the first
  charge implicitly opening the tray. Charging into a `PENDING` batch is
  refused with a message naming the remedy.
- **`flux_recipe_id` lives on `Crucible`, not `Batch`** — a correction to the
  original roadmap sketch, made before any schema was written. One furnace
  load routinely fires a silicate core sample beside a sulfide one, and each
  needs its own recipe; a batch is a shared furnace slot, not a shared
  formula.
- **A crucible's scaled reagent amounts are computed once at charge time and
  stored**, not recomputed from the recipe on every read — matches
  `fire_assay_result`'s "store what was actually weighed" precedent. If a
  recipe is edited afterward, an already-charged crucible still shows what a
  technician actually weighed out.
- **Position uniqueness (`UNIQUE(batch_id, position_row, position_col)`) is
  checked before insert**, same "clean 422, not a raw `IntegrityError`"
  discipline as client/project registration, alongside a domain-level
  `check_position` bounds check against `config.furnace_rows`/
  `furnace_columns` (both pre-set in Phase 0 anticipating exactly this).
- **`Crucible.sample_id` is `NOT NULL`; QC crucibles are out of scope for this
  pass.** No `crm_lots`/QC-material tables exist yet, so a nullable
  "this crucible has no sample, it's a blank" column would be an unused
  half-built branch. QC insertion is real remaining Phase 2 scope — see next
  actions.
- **`Batch`, `Crucible`, `FluxRecipe` are mutable, not append-only** — a new
  category alongside `audit_event`/`fire_assay_result`/`certificate`'s
  append-only tier. A batch's and a crucible's `status` advance in place
  through `domain.batch_lifecycle`, exactly like `sample.status` already does;
  a recipe is lab reference data, edited in place like `instrument`. What must
  never be an `UPDATE` — a crucible's frozen charge amounts once weighed out —
  is enforced in the service layer, the same way `sample.status`'s legal moves
  are enforced in `domain/lifecycle.py` rather than by revoking UPDATE from
  the whole `sample` table.
- **`AuditEvent.action = "transition"` is used for the first time** — a value
  the column's own docstring named as legal since Phase 0 (`"One of create,
  amend, supersede, transition"`) but nothing had written until a batch's
  status actually needed one.
- **A real modelling correction, caught before any schema was written, not
  after:** the original plan sketch put `flux_recipe_id` on `Batch`. Working
  through the physical reality — one furnace load, several different sample
  matrices — during design surfaced that a shared-slot/shared-formula model
  was wrong before it became a migration to walk back.
- **A real migration bug, caught by the reversibility check itself:** the
  first generated migration named `Batch.batch_number`'s unique constraint
  `"number"`, copying `Submission.submission_number`'s own literal constraint
  name. Postgres backs a `UNIQUE` constraint with an index, and index names
  are unique **per schema**, not per table — the migration failed outright
  with `relation "number" already exists` on `alembic upgrade head`. Fixed by
  dropping the explicit literal names on `FluxRecipe.name` and
  `Batch.batch_number` in favour of `unique=True`, which lets the declared
  naming convention (`uq_%(table_name)s_%(column_0_N_name)s`) generate a name
  that is unique across the whole schema by construction — the same approach
  `Instrument.name` already used, just not one this session's first draft
  followed.

### Database — `src/msa_lims/db/`
- 14 tables: `client`, `project`, `drill_hole`, `submission`, `sample`,
  `instrument`, `lab_user`, `audit_event`, `fire_assay_result`, `certificate`,
  `certificate_result`, `flux_recipe`, `batch`, `crucible`.
- `7172b2adeb7e` — initial schema.
- `a64c168cff52` — audit events.
- `b1d0c4e77a10` — **append-only grants**. Creates `msa_app` with no
  UPDATE/DELETE on `audit_event`, and deliberately **no `ALTER DEFAULT
  PRIVILEGES`**: a table added in a later migration gets no grants until someone
  decides, in a reviewable diff, whether it is mutable or append-only.
- `450d413603cf` / `9a1c2e6f4b3d` — the `fire_assay_result` table and its
  append-only grants, in separate migrations (schema and mutability are
  reviewed as separate decisions everywhere in this repo).
- `f73c45982855` / `2d5f8a17c930` — the `certificate` and `certificate_result`
  tables and their append-only grants, same split.
- `f9845a996c0d` / `e4d969607d8c` — the `flux_recipe`, `batch`, and `crucible`
  tables and their **mutable** grants (`SELECT, INSERT, UPDATE, DELETE`,
  matching the `MUTABLE_TABLES` tier `b1d0c4e77a10` already defined), same
  schema/mutability split, first use of that tier since Phase 0.
- Enums stored as VARCHAR with a CHECK rather than Postgres native enums, so
  removing a vocabulary value is not a table rewrite.
- `submission.declared_sample_count` records what the client's paperwork claimed
  separately from what arrived. A discrepancy is a conversation, not a fix.

### HTTP API — `src/msa_lims/web/`
- `GET /health` — reports database and QC Sentinel **separately**. Sentinel
  unreachable is `degraded` at HTTP 200, because the lab assays samples fine
  without it; only the database being down is `unhealthy`. Verified live in both
  states.
- Domain refusals mapped to distinct status codes: **403** find someone with
  authority, **409** the sample moved under you, **422** the request is wrong.
- **Auth is implemented** (see above) and every write endpoint depends on
  `ActorDep`. `POST /api/submissions` is the first to exist, and the first to
  exercise a `domain/lifecycle.py` role check through real HTTP: 201 on
  success, 403 for `client`, 404 for an unknown client, 422 with every
  validation problem in one list.
- Ten write endpoints, and now five read endpoints:
  `GET /api/samples` (the list), `GET /api/samples/{id}` (a sample's current
  result and every certificate that names it), `GET /api/certificates/{id}`
  (metadata, sharing the exact certified-samples query the issuance response
  uses), `GET /api/certificates/{id}/pdf` (the raw document), and — new this
  phase — `GET /api/batches/{id}` (a batch and its crucibles, ordered the way
  a technician reads a tray). `POST /api/fire-assay-results` was the first
  write to compute a response rather than only echo what it stored;
  `POST /api/batches/{id}/crucibles` is the second, computing scaled reagent
  amounts from a recipe rather than storing raw input.

### Frontend — `frontend/`
- React 18 + TypeScript + Vite, `strict` plus `noUncheckedIndexedAccess` and
  `exactOptionalPropertyTypes`.
- Dev server proxies `/api` and `/health` to the backend, so there is one origin
  and no CORS configuration to get wrong in dev and differently wrong later.
- **Routing landed for the first time** — `react-router-dom` had been a
  dependency since Phase 0 but unused; `App.tsx` is now a thin shell (nav +
  `<Routes>`) over `pages/SampleList.tsx`, `pages/SampleDetail.tsx`, and the
  original walking-skeleton screen, relocated to `pages/SystemStatus.tsx` and
  demoted from landing page to a reachable-but-secondary `/status` route now
  that there is something more useful to land on. `main.tsx` opts into
  React Router's v7 future flags specifically to keep the console clean —
  cheap, and a portfolio demo's console is part of what a reviewer sees.
- **`pages/SampleList.tsx`** — a table over `GET /api/samples`: sample id
  (linked), type, status (colour-coded pill), client, submission. No filter
  UI yet even though the backend already accepts `client_id`/`status` query
  params — building filter controls with nothing yet to filter *against*
  (no client-listing endpoint exists) would have been UI for a use case the
  API can't actually serve.
- **`pages/SampleDetail.tsx`** — a sample's status, type, submission/hole
  context, its current fire assay result rendered through the *existing*
  `formatMeasured` helper (unchanged since Phase 0 — a censored grade shows
  as `<0.01 g/t` here for the same reason it always has, not a new
  code path), and every certificate that names it with a direct
  `/api/certificates/{id}/pdf` download link. A 404 from the API is
  distinguished from any other failure (`ApiError.status`) and shown as "No
  sample with id N," not a generic error — the one place this frontend reads
  an HTTP status code rather than just success/failure.
- **`StatusPill` was extracted** from `App.tsx` into `components/StatusPill.tsx`
  and reused for both health-check statuses and sample statuses — the same
  three-tier colour system (`--ok`/`--warn`/`--bad`) now covers both
  vocabularies via extended CSS selector groups (`.pill-assayed`,
  `.pill-reported` join `.pill-ok`; `.pill-received` through
  `.pill-in_assay` join `.pill-degraded`; `.pill-rejected` joins
  `.pill-unavailable`) rather than a second, parallel pill system.
- **`.components` was renamed `.detail-grid`** — the same dt/dd grid layout
  now serves the health-check screen and both sample screens; a name specific
  to "system components" stopped describing what it actually was the moment
  a second screen needed the identical layout.
- `types.ts` mirrors the wire format including the censored-value distinction,
  with `formatMeasured` so a non-detect cannot be rendered as its null value —
  now also carrying `SampleListItem`, `SampleDetail`, `FireAssayResult`, and
  `CertificateReference`, hand-written like everything else here with the
  same standing note that these should come from `/openapi.json` once the API
  stops moving.

### Tests — 377
- **Unit** (178): units and dimensions, censored values, assay arithmetic,
  sample labels and intervals (including the `format_hole_id`/
  `canonical_hole_id` identity with a parsed sample's own `hole_id`), the
  sample state machine, OIDC token verification against a real self-signed
  keypair, the auth dependency exercised through a real FastAPI app
  (`TestClient`) across dev-header and OIDC modes, Certificate of Analysis PDF
  byte-determinism (identical content twice → identical bytes; different
  content → different bytes; an 80-sample page-break case), the grade-
  rounding fix (a non-terminating division rounds to three decimal places
  under `ROUND_HALF_EVEN`; a clean value is unaffected; a non-detect's
  detection limit is never rounded; the stored full-precision value is
  untouched by rendering) — and, new this phase, flux charge scaling (7 —
  the exact-nominal-weight identity, doubling and halving the sample weight,
  an unused reagent staying zero, non-positive inputs refused) and the batch
  state machine (13 — the full linear walk, every skip and every backward
  move refused, an insufficiently-privileged role refused, `FUSED`/`CUPELLED`
  bulk-mapping to a crucible status and every other transition mapping to
  `None`, and the furnace-position bounds check on all four edges).
- **Property** (17, Hypothesis): conversion round-trips within working
  precision; mass conversions exact; substitution always lands within the limit;
  the inverse grade calculation recovers its input; contiguous intervals never
  conflict; generated labels parse back to their parts.
- **Integration** (182, real Postgres): the append-only grants proven against
  the actual application role, now also proving `batch` remains genuinely
  mutable under the same role (11 tests); submission intake against the
  service directly and through the real HTTP app (26 tests); client and
  project registration (21 tests); drill hole registration (16 tests,
  including the hole-canonicalisation-matches-a-drill-sample proof); fire
  assay result entry (26 tests — the computed grade, the ASSAYED transition,
  every supersession refusal including anti-branching, and a direct proof
  that Postgres refuses `UPDATE`/`DELETE` against `fire_assay_result`);
  Certificate of Analysis issuance (25 tests — issuance, the
  ASSAYED→REPORTED transition, every validation refusal including
  cross-client isolation and anti-branching on the amendment chain, the
  hash-verified download, and a direct proof that Postgres refuses
  `UPDATE`/`DELETE` against `certificate` too); sample and certificate lookup
  (9 tests — a fresh sample with no result or certificates, the current
  result surfacing after a correction supersedes the original, every
  certificate a sample was ever named on staying listed after an amendment, a
  404 for each unknown id, and the `POST`/`GET` responses for one certificate
  asserted byte-for-byte equal); sample listing (7 tests — an empty lab lists
  nothing, a listed row carries its client and submission but deliberately
  not its grade or certificates, newest-first ordering, `client_id`/`status`
  filtering, and an invalid status value refused with 422 before it reaches
  the service); flux recipe registration (9 tests — registration, the role
  gate, a duplicate name refused, a negative reagent amount refused at the
  Pydantic layer, the audit event); furnace batching (30 tests — sequential
  batch numbering, opening restricted to bench roles, charging refused before
  a batch is `CHARGING`, flux scaled correctly onto the stored crucible row,
  a sample already `IN_ASSAY` refused a second charge, an occupied position
  refused, an out-of-tray position refused as the domain error (not a generic
  422), an unknown recipe/batch/sample each refused with the right error
  type, a zero sample weight surfacing the real `FluxCalculationError`, the
  full linear status walk with crucible status bulk-advancing at `FUSED`/
  `CUPELLED`, firing an empty batch refused, skipping a stage refused, batch
  detail ordering crucibles by tray position — both service-level and,
  end-to-end through HTTP, the full open→charge→fire→complete walk and the
  precondition/position/role refusals as real status codes).

### Verified live
The stack driven through a browser: Vite dev server → proxy → FastAPI →
Postgres, rendering `healthy` with the database connected and no console errors.
Restarting the API with `MSA_SENTINEL_ENABLED=true` against a Sentinel that is
not running renders `degraded` — not `unhealthy`, and still HTTP 200.

Auth verified live against the running server: `GET /api/me` with no headers
returns `{"name": "dev@localhost", "role": "analyst"}`; with `X-Actor` and
`X-Actor-Role: lab_manager` it returns that identity; an unknown role is
refused with **400** naming the valid list.

Submission intake verified live: `POST /api/submissions` as `analyst` with two
soil samples returns **201** with `submission_number: "SUB-2026-0001"` and both
samples `status: "received"`; the same request as `client` returns **403**; a
sample labelled `"garbage"` returns **422** with the parser's own message
naming both accepted label shapes; `SELECT * FROM lab_user` afterward shows the
actor provisioned by subject, with the role from the request headers.

Client and project registration verified live, chained end to end: a
`lab_manager` registers a client (`201`, code normalised to `"MSA"`), the same
manager registers a project under it (`201`), an `analyst` attempting to
register a client is refused (`403`), re-registering the same code returns
**422** naming the conflict directly (`"client code 'MSA' is already in use"`),
and a submission posted against the freshly registered `client_id`/`project_id`
succeeds with **201**.

Drill hole registration verified live, extending the chain one link further:
an `analyst` registers a hole as `"msa-24-001"` under the freshly registered
project (`201`, stored as `"MSA-24-001"`); a `client` role attempting the same
is refused (`403`); re-registering `"msa-24-001"` (different case, same
canonical hole) returns **422** naming the project and the hole directly; an
out-of-range `dip_degrees: "-95"` is refused before it reaches the service,
at the Pydantic layer; and a submission posted with a drill sample labelled
`"MSA-24-001-142.50_144.00"` resolves to `drill_hole_id: 1` — the registered
hole, matched purely by the canonicalisation agreeing, with the interval
correctly split into `from_depth_m`/`to_depth_m`. This was the first time the
entire spine — client, project, drill hole, and a drill sample against all
three — ran purely through HTTP with no direct database insert anywhere in
the chain.

Fire assay result entry verified live, all the way to a computed grade: a
`0.150` mg bead from a `30` g portion posted as `analyst` returns **201**
with `au: {"value": "5.000", "censored": false, "unit": "g/t", ...}` —
computed, not supplied; a `prep_tech` attempt is refused with **403**; a
second new result against the same sample returns **422** naming the
existing result by id and telling the caller to supersede it; a `lab_manager`
then supersedes it with a corrected bead weight and a stated reason, and the
new row's `au.value` differs from the original's, proving the recalculation
actually ran on the corrected input; attempting to supersede the now-
superseded original a second time is refused with **422**, "only the current
result can be superseded"; and, with the same `msa_app` credentials the
deployed application holds, a direct `psql` `UPDATE fire_assay_result SET
au_value = 999` was refused by Postgres itself with `permission denied for
table fire_assay_result` — not a service-layer promise, a database-enforced
one, checked live exactly as `test_append_only.py` checks it for
`audit_event`.

Certificate of Analysis issuance verified live end to end, and this pass is
what caught the grade-rounding defect described above before it shipped. A
`lab_manager` issues a certificate for a sample with a `0.150` mg bead
(**201**, `COA-2026-0001`, a real 64-character `pdf_sha256`); downloading
`GET /api/certificates/1/pdf` returns bytes whose independently-computed
`sha256` matched the header exactly; the sample's status in the database
changed from `assayed` to `reported`; an `analyst` attempting to issue a
certificate is refused with **403**. Re-run with a bead weight that does
**not** divide evenly (`0.160` mg over `30` g) first reproduced the defect —
the downloaded PDF showed `5.333333333333333333333333333333333 g/t` — and
after the fix, the identical live sequence showed a clean `5.333 g/t` while
`GET /api/fire-assay-results`' own JSON kept the full stored precision,
confirming the fix is display-only. The amendment path was then verified: the
underlying result was superseded with a corrected bead weight, the
certificate was re-issued referencing the original by `supersedes_id` with a
stated reason (**201**, `COA-2026-0002`, PDF showing "This certificate
supersedes COA-2026-0001. Reason: …"), and a second attempt to supersede the
now-superseded original was refused with **422**. Finally, with the deployed
application's own `msa_app` credentials, a direct `psql`
`UPDATE certificate SET notes='tampered'` was refused with
`permission denied for table certificate`.

Sample and certificate lookup verified live, end to end: `GET
/api/samples/{id}` on a freshly submitted sample returns `current_result:
null` and `certificates: []`; after a fire assay result and a certificate are
issued against it, the same `GET` shows `status: "reported"`, the current
grade, and the certificate it was named on; `GET /api/certificates/{id}`
returned a JSON body **byte-for-byte identical** to what the `POST` that
issued it had already returned, in a second, independent request; and both
`GET /api/samples/999999` and `GET /api/certificates/999999` return **404**.

The React screens verified live through the browser, the golden path and the
edges both: two samples seeded through the real API (one with a fire assay
result and an issued certificate, one still `received`) rendered on
`/samples` as a table with correctly colour-coded status pills and no
console errors; clicking the reported sample opened `/samples/1` and showed
`Au: 5.000 g/t`, the method, bead weight, and portion exactly as entered, and
a `Download PDF` link; that link was fetched directly from the rendered page
(not just present in markup) and returned real PDF bytes — `%PDF-` magic
header, `content-type: application/pdf` — through the Vite dev-server proxy;
the still-`received` sample rendered "No result yet." and "Not yet on a
certificate." rather than blank sections; navigating to `/samples/999999`
showed "No sample with id 999999," distinguishing the 404 case from a
generic failure; `npm run build` and `tsc --noEmit` both passed clean
throughout.

Furnace batching verified live end to end via curl against a running server
seeded through the same session: a client, a two-sample submission, and a
"Standard Silicate" flux recipe (60/90/30/15/3/0 g, calibrated at 30 g) were
registered first. An `analyst` opened a batch (**201**, `BATCH-2026-0001`,
`status: "pending"`); charging a crucible into it before advancing to
`CHARGING` was refused with **422** naming the batch and the exact remedy
("open it for charging before assigning crucibles"), proven on a *second*
batch left deliberately `PENDING` so the first batch's own happy path stayed
uninterrupted. After `PATCH .../status {"status": "charging"}`, a sample was
charged at position `2-3` with a `45` g portion — one and a half times the
recipe's 30 g nominal — and every reagent in the response came back scaled by
exactly that factor (`litharge_g: 60 → 90.0`, `soda_ash_g: 90 → 135.0`,
`borax_g: 30 → 45.0`, `silica_g: 15 → 22.5`, `flour_g: 3 → 4.5`); charging a
second sample into the same position `2-3` was refused with **422** naming
the exact occupied slot. The batch was then walked through every remaining
status in order — `in_fusion → fused → in_cupellation → cupelled →
completed` — each `PATCH` returning **200** with the new status; `GET
/api/batches/1` afterward showed the crucible's own status bulk-advanced to
`"cupelled"` without a separate call, and the sample's status was `in_assay`
(not yet `assayed` — batch completion charges and fires material, it does not
itself produce a result). A `prep_tech` was then confirmed able to open a
batch (**201** — bench work, same tier as charging), while an `analyst`
attempting to register a flux recipe was refused with **403** naming
`lab_manager, supervisor` — proving `MAY_CONFIGURE_LAB` is enforced as its
own, narrower gate. Finally, `POST /api/fire-assay-results` was called
against the now-`in_assay` sample with a `0.220` mg bead from the actual `45`
g charged portion: it succeeded (**201**), computed a real grade, and moved
the sample to `assayed` — proving Phase 1's result-entry path and Phase 2's
batching path compose cleanly, with no conflict over who owns the sample's
status. Demo data was truncated from the dev database afterward.

---

## Decision log

| Date | Decision | Why |
|---|---|---|
| 2026-08-22 | Build MSA LIMS as a **separate repo and system** rather than expanding QC Sentinel | Sentinel's PRD names sample-of-record management a non-goal and tracks "scope creep into LIMS" as a risk. Its strongest claims — append-only by grant, no write-backs, advisory disposition — only mean something for a system that sits *beside* the system of record. |
| 2026-08-22 | Its **own** Postgres container on port 5435, not a second database in Sentinel's | A stranger must be able to clone this repo alone and `docker compose up`. Sharing a container would also mean one system's `docker compose down -v` destroys the other's data. Revisit only if the OCI box runs short of memory. |
| 2026-08-22 | **Copy** the domain patterns from Sentinel rather than extract a shared library | A shared package couples two repos' release cycles for no benefit at this scale. "Two systems that share nothing but a file format" is also the more honest integration story. |
| 2026-08-22 | React for this UI, though Sentinel is deliberately server-rendered and JS-free | Different systems, different constraints. Sentinel's no-JS rule exists for air-gapped plants; this is the office-side system, and the React app is where the JavaScript background shows while the backend exercises Python. |
| 2026-08-22 | Append-only grants land in **Phase 0**, not a later hardening phase | Retrofitting immutability onto a schema that was mutable for three months means backfilling an audit trail that does not exist. It is structural or it is theatre. |
| 2026-08-22 | **No `ALTER DEFAULT PRIVILEGES`** in the grants migration | A new table should be inaccessible until someone decides whether it is append-only. Discovering the omission is a loud failure on first use; the alternative is a results table that quietly turned out to be editable. |
| 2026-08-22 | Depth intervals are **half-open** (`from` inclusive, `to` exclusive) | Contiguous sampling is the normal case. Under a closed convention every contiguous run in the database would trip the overlap check, and the fix would be a tolerance fudge. |
| 2026-08-24 | Auth ported and wired **before any write endpoint**, not alongside the first one | Retrofitting auth onto endpoints already shipped without it is how a dependency gets skipped on one route and nobody notices. Every write endpoint from Phase 1 onward is required to depend on `ActorDep` from the moment it is written. |
| 2026-08-24 | `CLIENT` placed at the **bottom** of the OIDC privilege order, not left unordered | Sentinel's `AUDITOR` sits at the bottom of its own privilege list for the same reason: a person who happens to hold both an external and an internal group must never be resolved to the more privileged role by accident of ordering. |
| 2026-08-24 | `LabUser` rows are **provisioned on first sight**, keyed on `Actor.subject`, rather than requiring a separate admin-creates-user step first | An OIDC-fronted system's whole point is that the provider is the source of truth for identity; refusing a genuinely authenticated person's first write until an admin pre-creates a row would fight that. `LabUser.role` is a courtesy mirror only — authorisation never reads it. |
| 2026-08-24 | Every write endpoint validates the **entire request before writing anything** — no partial submission on a mixed-quality batch | Matches Sentinel's ingestion posture and `find_overlaps`'s own "report every conflict, not the first" design. A test asserts directly that a batch with one bad label among good ones leaves zero rows behind. |
| 2026-08-24 | An unregistered drill hole **refuses** the sample rather than auto-creating a stub `DrillHole` | Mirrors Sentinel's FR-5 (unregistered CRM lots quarantine, never get invented). A hole created from a sample label alone would have null coordinates, null depth, null dip — geological context the system would then be lying about having. |
| 2026-08-24 | Client/project registration restricted to a **new, narrower** role tier (`MAY_MANAGE_ACCOUNTS` = supervisor, lab_manager) rather than reusing `BENCH_ROLES` | Receiving physical material at the door and setting up a billing relationship or a drilling program are different kinds of authority. Reusing `BENCH_ROLES` would let a prep technician onboard a client, which nothing about "prep technician" implies. |
| 2026-08-24 | `ClientNotFoundError` **hoisted** out of `submissions/service.py` into the new `clients/service.py`, re-exported for backward compatibility | Both submission intake and project registration ask "does this client exist?" Two separately defined classes with the same name is a real bug source — a caller catching one type would silently miss the other. |
| 2026-08-24 | Uniqueness (client code, client name, project name within a client) checked with a `SELECT` **before** the `INSERT`, not left to the database's UNIQUE indexes | There is no global `IntegrityError` handler yet. Without the pre-check, a duplicate registration would surface as an unhandled 500 instead of a clear 422 naming the conflict. |
| 2026-08-24 | Drill hole registration restricted to `BENCH_ROLES`, not `MAY_MANAGE_ACCOUNTS` — the tier client/project registration uses | A hole's collar coordinates typically arrive with the drill log accompanying a core shipment, the same moment a submission is received, not as a business decision about who the lab works for. Client/project setup and hole logging are different kinds of authority even though both are "reference data." |
| 2026-08-24 | Extracted `format_hole_id`/`canonical_hole_id` into `domain/sample_id.py` rather than duplicating the format string in the registration service | `SampleIdentity.hole_id` already computed this string from a parsed sample label. Registration needed to produce the identical string from a directly-typed hole label. Two independently-written format strings that happened to agree would be one refactor away from silently disagreeing and breaking every drill-sample lookup. |
| 2026-08-24 | `ProjectNotFoundError` added to `clients/service.py`, not `drill_holes/service.py` | Same reasoning as `ClientNotFoundError`'s placement: the module that owns `Project` owns the error for "this one doesn't exist," so every caller — submissions, drill holes, anything else later — gets the same answer type. |
| 2026-08-25 | Fire assay result entry supports **only `FIRE_ASSAY_GRAVIMETRIC`**, with no `method` field on the request and no validation branch for AAS/ICP-MS | Those methods read a concentration off a calibration curve, not a bead weight — a completely different input shape. Adding a `method` parameter that only one value is honoured for would invite exactly the "designed for a hypothetical future requirement" trap; AAS entry gets its own endpoint with its own shape when it is built. |
| 2026-08-25 | "Current result" for a sample is computed **by exclusion** (the row nothing else's `supersedes_id` names), not a stored `is_current` flag | A flag needs to be flipped in two places every time a correction lands and can drift from reality if one write forgets to. Exclusion has nothing to keep in sync — it is derived from the same facts every time. |
| 2026-08-25 | Superseding a row that is not currently the chain's head is **refused**, not permitted as a branch | Mirrors QC Sentinel's rule against double-replacement in a re-assay chain. Allowing it would let two different "corrections" of the same original both claim to be current, and nothing in the schema says which one wins. |
| 2026-08-25 | Entering a sample's first result moves it straight to `ASSAYED`, **bypassing `domain/lifecycle.py`'s `Transition` table** rather than adding a fake transition to it | The legal path to `ASSAYED` requires `IN_ASSAY`, which nothing can reach yet — furnace batching doesn't exist. Adding a permissive `RECEIVED → ASSAYED` row to the shared `TRANSITIONS` tuple would misrepresent a real legal path once Phase 2 lands. A narrow, honestly-documented guard local to this service says exactly what it is: a Phase 1 simplification, not lab policy. |
| 2026-08-25 | The append-only grants for `fire_assay_result` are their **own migration** (`9a1c2e6f4b3d`), not folded into the table-creation migration | Matches the existing split between `b1d0c4e77a10` and the schema migrations before it: schema and mutability are separate decisions, each visible on its own in the migration history. |
| 2026-08-25 | A certificate covers an **explicit list of `sample_ids`**, not an implicit "every sample in submission X" | Real certificates sometimes span multiple submissions or report a ready subset while other samples are still pending. Tying the model to one submission would have made those the wrong shape rather than merely unsupported. |
| 2026-08-25 | **No "only one current certificate per client" rule**, unlike `fire_assay_result`'s "only one current result per sample" | A client legitimately holds many independent certificates over time — one per batch reported. Supersession only guards a single chain against branching; it was never meant to cap how many documents a client has. |
| 2026-08-25 | The PDF is stored **inline** (`pdf_bytes` + `pdf_sha256`) rather than in a dedicated content-addressed blob store | A full write-once, hash-verified blob store (mirroring QC Sentinel's `storage/blob.py`) is real infrastructure this Phase does not yet need anywhere else. `pdf_sha256` still gives genuine content-addressing and read-time verification without building the abstraction before a second use case (raw exports, attachments) exists to justify it. |
| 2026-08-25 | Issuing a certificate **actually calls** `domain.lifecycle.check_transition` for the `ASSAYED → REPORTED` move, rather than bypassing it the way fire assay result entry had to | The legal path exists and is reachable here (the sample is genuinely `ASSAYED` by this point) — this is exactly the scenario `check_transition` was built for, unlike result entry's `RECEIVED → ASSAYED` shortcut, which had no real transition to route through. |
| 2026-08-25 | A computed grade is **rounded only at the point a certificate presents it to a person** (`ROUND_HALF_EVEN`, 3 decimal places), never in the stored `fire_assay_result.au_value` | Caught live: a non-terminating division (0.160 mg / 30 g) printed 34 digits of `Decimal` artifact on a signed document. Rounding in the domain calculation itself would have silently discarded real precision for the common case where the division *does* terminate cleanly; rounding only at display time keeps the stored, audit-relevant value exact while fixing what a human actually reads. |
| 2026-08-25 | `SampleNotFoundError` **stays** in `fire_assay_results/service.py`; `samples/service.py` imports it rather than the reverse | Breaks precedent (`ClientNotFoundError`/`ProjectNotFoundError` were both hoisted to the module owning the entity) deliberately: `samples/service.py` already depends on `fire_assay_results/service.py` for `current_result`, and hoisting the exception the other way would have created an import cycle for no benefit the one-way dependency doesn't already give. |
| 2026-08-25 | `CertificateOut.sample_count` **replaced** with a real `samples` list read back from `certificate_result`, not kept alongside it | The int was computed from `len(request.sample_ids)` at issuance time and never actually verified against what got written — a number that could theoretically lie. A breaking response-shape change, judged acceptable now because nothing outside this repo's own tests consumed the old shape yet. |
| 2026-08-25 | `get_certified_samples` is **one query**, called by both the certificate `POST` response and the new `GET` | The alternative — the `POST` route building its response from in-memory objects left over from creation, the `GET` route querying fresh — would let the two responses drift apart on a future refactor with no test catching it. A live check confirms they are byte-for-byte identical today; sharing the query is what keeps that true. |
| 2026-08-25 | Added `GET /api/samples` (a listing endpoint) as part of "build the sample list screen," not as separate, unrequested scope | A list screen with nothing to list isn't a screen. The gap was already named in the previous phase's own open questions, anticipating exactly this. |
| 2026-08-25 | `list_samples` deliberately **omits the current grade and certificates** from each row | Those need a per-sample lookup each; a hundred-row list would otherwise cost a hundred extra queries for facts the detail screen already shows on click. A test asserts the keys are genuinely absent, not just unused. |
| 2026-08-25 | No filter UI built for `client_id`/`status`, even though both already work on the backend | There is no client-listing endpoint to populate a filter dropdown from. Building filter controls against a value set the API cannot yet supply would be UI for a use case that doesn't exist yet. |
| 2026-08-25 | `StatusPill` **extracted and reused** for sample statuses, extending the existing three-tier colour system rather than inventing a second one | `assayed`/`reported` map to the same "good" tier `healthy` already used; `received` through `in_assay` map to the same "in progress" tier as `degraded`. Two vocabularies, one set of CSS tokens — matches this codebase's own "choose neutrals, don't default to them" discipline at the component level. |
| 2026-08-25 | `.components` renamed to `.detail-grid` | The class was never really about "system components" — it was a dt/dd grid layout that a second, unrelated screen needed identically. A name tied to its first caller stops being honest the moment a second one arrives. |
| 2026-08-25 | React Router's v7 future flags enabled immediately, not deferred | One line, zero behaviour change today, and it removes two console warnings that would otherwise sit in every future screenshot and demo recording of this app. |
| 2026-08-25 | Crucible charging **bypasses** `domain.lifecycle`'s `READY_FOR_ASSAY -> IN_ASSAY` transition, the same way fire assay result entry bypasses the table | Investigated before writing code: `check_transition` is called from exactly one place in the whole codebase (`ASSAYED -> REPORTED`), so no sample can currently reach `READY_FOR_ASSAY` through the modelled path — prep-stage tracking doesn't exist yet. Routing charging through `check_transition` honestly would only work for a sample that reached `READY_FOR_ASSAY` some other way that also doesn't exist yet. |
| 2026-08-25 | `flux_recipe_id` lives on **`Crucible`, not `Batch`** — a correction to the original roadmap sketch | One furnace load routinely fires a silicate sample beside a sulfide one; each needs its own recipe. A batch is a shared furnace slot, not a shared formula. Caught during design, before any migration was written. |
| 2026-08-25 | A new role tier, `MAY_CONFIGURE_LAB`, defined **separately** from `MAY_MANAGE_ACCOUNTS` even though both currently name the same two roles | Defining what goes into a furnace and setting up a client's billing relationship are different kinds of authority, held by the same two roles only because this lab is small. Matches the standing precedent (`BENCH_ROLES` vs. `MAY_REJECT`, `MAY_ENTER_RESULTS` vs. `MAY_SIGN_CERTIFICATE`) of keeping distinct authority domains as distinct constants even where membership currently overlaps. |
| 2026-08-25 | A batch's own status machine (`domain/batch_lifecycle.py`) is **strictly linear** — no branch, no way back | A batch describes a furnace run that already physically happened; unlike a sample (which can be returned for re-assay), there is no honest way to "un-fire" a batch. A re-assay charges the sample into a *new* batch instead. |
| 2026-08-25 | `Crucible.sample_id` is `NOT NULL`; QC-material crucibles are **out of scope** for this pass | No `crm_lots` or QC-material tables exist yet. A nullable "this crucible has no sample" column with no write path to ever populate it would be exactly the kind of half-finished branch this codebase avoids. |
| 2026-08-25 | `flux_recipe`, `batch`, `crucible` are **mutable**, joining `client`/`sample`/`instrument`'s grant tier, not append-only | A batch's and a crucible's status advance in place, matching `sample.status`; a recipe is edited in place, matching `instrument`. What must never be an `UPDATE` (a crucible's frozen charge once weighed) is a service-layer discipline, not a grant — the same split `sample.status`'s legal moves already rely on. |
| 2026-08-25 | `Batch.batch_number` and `FluxRecipe.name` use `unique=True` on the column, not an explicit `UniqueConstraint(..., name="number")` | The first-drafted migration copied `Submission`'s literal constraint name `"number"` and failed outright on `alembic upgrade head` with `relation "number" already exists` — Postgres unique-constraint index names are unique per schema, not per table. Letting the declared naming convention derive a table-qualified name (as `Instrument.name` already did) avoids the whole collision class rather than requiring every future short literal name to be checked against every other table's. |

---

## Next actions (Phase 1 — the spine)

1. ~~**Authentication before the first write endpoint.**~~ **Done 2026-08-24** —
   OIDC verification and the dev-header shim both land, wired through
   `ActorDep`, verified live.
2. ~~**Submission creation with sample rows.**~~ **Done 2026-08-24** —
   `POST /api/submissions`, full-batch validation, drill-hole resolution,
   overlap checking against both the new batch and prior samples, `LabUser`
   provisioning, one audit event per row. 26 new tests, verified live.
3. ~~**Client and project registration endpoints.**~~ **Done 2026-08-24** —
   `POST /api/clients`, `POST /api/projects`, both restricted to
   `MAY_MANAGE_ACCOUNTS`. 21 new tests; verified live end to end with a
   submission posted against a freshly registered client and project.
4. ~~**Drill hole registration endpoint.**~~ **Done 2026-08-24** —
   `POST /api/drill-holes`, restricted to `BENCH_ROLES`. Canonicalises through
   the same `format_hole_id` a parsed sample's `hole_id` uses, so a hole
   registered in one case is provably the row a drill sample in another case
   resolves to. 16 new tests; verified live through the entire chain —
   client → project → drill hole → a drill sample resolving its hole — purely
   through HTTP. **Every reference-data registration endpoint the spine needs
   now exists.**
5. ~~**Fire assay result entry against `domain/assay.py`.**~~ **Done
   2026-08-25** — `POST /api/fire-assay-results`, gravimetric only, stored
   append-only with supersession, one `audit_event` per write (create or
   amend). 26 new tests, including a direct proof against the real
   application role that Postgres refuses `UPDATE`/`DELETE`. Verified live
   end to end: a computed grade, a role refusal, a duplicate-result refusal,
   a correction with a recalculated grade, an anti-branching refusal, and the
   database itself refusing a tampering attempt with the deployed
   application's own credentials.
6. ~~**Certificate of analysis: versioned row, byte-deterministic PDF, amended
   never overwritten.**~~ **Done 2026-08-25** — `POST /api/certificates`,
   `GET /api/certificates/{id}/pdf` (the system's first `GET` endpoint), both
   verified live. 31 new tests, including a unit-level determinism proof and
   a direct proof against the real application role that Postgres refuses
   `UPDATE`/`DELETE`. Live verification caught and fixed a real grade-
   rounding defect before it shipped — see the decision log.
7. ~~**`GET /api/samples/{id}` and `GET /api/certificates/{id}`.**~~ **Done
   2026-08-25** — a sample's current result and every certificate that names
   it; a certificate's full metadata including its certified samples, sharing
   the exact query the issuance response uses. 9 new tests. Verified live:
   the `POST` and `GET` responses for one certificate matched
   byte-for-byte, and a sample's `GET` correctly showed `reported` and the
   certificate that named it after issuance. **The API Phase 1 needs is now
   complete; only React screens remain.**
8. ~~**Sample list and detail screens in React.**~~ **Done 2026-08-25** —
   `pages/SampleList.tsx`, `pages/SampleDetail.tsx`, routing landed for the
   first time (`react-router-dom` had sat unused since Phase 0), the original
   status screen relocated to `/status`. Required adding `GET /api/samples`
   first — a list screen had nothing to list without it. 7 new backend tests
   for the listing endpoint. Verified live: real seeded data rendering
   correctly in both the table and detail views, a working certificate PDF
   download fetched directly from the rendered page (not just present in
   markup), the empty/no-result/no-certificate states, the 404 state
   distinguished from a generic failure, and a clean production build.
   **Phase 1 — the thinnest complete thread the original roadmap called
   for — is done.**

## Next actions (Phase 2 — fire assay batching)

1. ~~**Furnace batches, crucibles with position constraints, flux recipes with
   matrix-scaled amounts.**~~ **Done 2026-08-25** — `domain/flux.py`,
   `domain/batch_lifecycle.py`, `flux_recipes/service.py`,
   `batches/service.py`; `POST /api/flux-recipes`, `POST /api/batches`,
   `POST /api/batches/{id}/crucibles`, `PATCH /api/batches/{id}/status`,
   `GET /api/batches/{id}`. 60 new tests. Verified live end to end: a
   recipe registered, a batch opened, charging refused before `CHARGING`, a
   crucible charged with correctly scaled reagents, a position collision
   refused, the full furnace walk to `completed` with crucible status
   bulk-advancing, and a fire assay result entered against the now-`in_assay`
   sample — proving this phase composes with Phase 1 rather than
   conflicting with it.
2. **QC insertion policy.** No `crm_lots` or QC-material tables exist yet, and
   `Crucible.sample_id` is `NOT NULL` — a QC crucible (a blank, a CRM, a
   duplicate) has no row shape to occupy. Needs its own design pass: at
   minimum a `QcMaterialType`-tagged crucible variant (already anticipated by
   `domain/enums.py`'s `QcMaterialType` since Phase 0) and a decision about
   whether QC insertion is enforced (a batch cannot fire without one) or
   merely recorded.
3. **Wire `fire_assay_result` to the crucible it came from.** Result entry
   today still takes a raw `gold_bead_mg`/`sample_weight_g` typed directly
   into the request, with no reference to the crucible a sample was actually
   charged into — meaning the portion weight entered at result time is not
   provably the same one recorded when the crucible was charged. A
   `crucible_id` on `FireAssayResult` (or deriving the portion from the
   crucible instead of re-entering it) is the natural next increment,
   deliberately deferred this session to keep the vertical slice thin — see
   the module docstring in `batches/service.py` for the current, honest
   boundary between the two write paths.
4. **Per-crucible weighing (`lead_button_weight_mg`, `prill_weight_mg`,
   `parting_acid_volume_ml`) after cupellation.** `CrucibleStatus.PARTED` and
   `.WEIGHED` exist in the vocabulary (Phase 0) but have no write path — this
   session deliberately kept `Crucible` lean (position, flux charge, status)
   rather than adding columns with nothing to populate them yet, matching the
   Phase 1 discipline of not building a dead branch ahead of its use.

## Open questions

- **Which balance sensitivity is real?** `gravimetric_grade` takes it as a
  parameter, but the value should come from the instrument record once
  `instrument` carries calibration data. Currently every caller must supply it
  — `fire_assay_results/service.py` still does not resolve this; it just
  passes through whatever the request gives, which may be nothing at all.
- **Does the lab report silver on every fire assay, or only on request?** Drives
  whether `silver_by_difference` is computed eagerly at bead entry or on demand.
  Still open — `fire_assay_result` has no silver columns yet.
- **No `analyst_id`/`instrument_id` link to a specific balance or AAS.**
  `fire_assay_result.analyst_id` records who entered it, but there is no
  `instrument_id` column recording which balance weighed the bead — meaning a
  future contamination or calibration-drift investigation has nothing to
  trace back to a specific piece of equipment. Deferred because `instrument`
  currently only tracks calibration due-dates, not per-weighing readings.
- ~~**No `GET` endpoint exists for a fire assay result, a submission, or a
  certificate's metadata.**~~ **Partly resolved 2026-08-25** — a sample's
  detail view now surfaces its current result inline (`GET
  /api/samples/{id}`), and a certificate's metadata is readable (`GET
  /api/certificates/{id}`). Still no standalone `GET` for a fire assay result
  by its own id, or for a submission's own metadata (only reachable via the
  sample it produced, or the original `POST` response) — neither has
  surfaced an actual need yet.
- **The PDF's inline `pdf_bytes` storage is a named simplification, not a
  commitment.** If raw ICP exports or attachments are ever added, a real
  content-addressed blob store (mirroring QC Sentinel's `storage/blob.py`) is
  the natural point to build one and migrate `certificate` onto it — not
  before, since nothing else needs it yet.
- **No automatic detection that a certificate has gone stale.** If a
  `fire_assay_result` a certificate already certified is later superseded,
  nothing flags the certificate as needing an amendment — a person has to
  notice and issue one manually (which works, and is tested), but the system
  does not surface the staleness itself.
- **No client-scoped authorisation on certificate download.** Any
  authenticated actor — including the `client` role — can `GET` any
  certificate's PDF by id. There is no per-client row-level scoping anywhere
  in this system yet; a real client portal would need it before this
  endpoint could be exposed to clients directly rather than only to lab
  staff. Now applies to the new `GET /api/samples/{id}` and `GET
  /api/certificates/{id}` too — same gap, not newly introduced by them.
- ~~**No listing endpoint anywhere.**~~ **Partly resolved 2026-08-25** —
  `GET /api/samples` exists now, with `client_id`/`status` filters. Still
  missing: "every certificate for client Y" and "every result for a sample"
  (only the *current* one is readable anywhere).
- **No client-listing endpoint, so the sample list has no filter UI.**
  `GET /api/samples` already accepts `client_id`, but there is nowhere to
  discover which client ids exist short of remembering one from creating it.
  A minimal `GET /api/clients` would unblock a real filter control.
- **No pagination on `GET /api/samples`.** `limit` exists (default 100, max
  500) but there is no `offset`/cursor — past the limit, older samples are
  simply unreachable through this endpoint. Not addressed because the
  demo-scale data this system has held so far has never approached the
  limit; revisit before seeding anything larger.
- **`GET /api/samples/{id}` shows only the current fire assay result, not the
  supersession history.** A sample corrected twice shows only the final
  grade; the earlier ones are still in the database (append-only, as
  designed) but nothing reads them back as a chain the way a certificate's
  `supersedes_id` can at least be followed one link at a time.
- **The React app sends no auth headers at all.** It relies entirely on
  `dev_headers` mode's no-header-supplied default (least-privileged
  `analyst`). There is no login screen, no token storage, and no path from
  the browser client to `oidc` mode — building one is real scope (a token
  exchange flow, storage, refresh) that nothing in Phase 1 needed since every
  verification ran as a trusted local developer. Whichever role actually
  needs to *sign a certificate* or *register a client* through the UI, rather
  than curl, will force this question.
- **Submission numbering.** `SUB-2026-0841` is invented. Needs the real
  convention before Phase 1 hardens it into stored data.
- **Does a sample ever move between submissions?** Currently `submission_id` is
  NOT NULL with no history. If re-submission happens, that is a chain, not an
  update.
- ~~**`Actor` vs. `LabUser`.**~~ **Resolved 2026-08-24** — see
  `current_lab_user` in `web/deps.py` and the decision log above.
- ~~**Client onboarding and project registration.**~~ **Resolved 2026-08-24** —
  see the Client and project registration section above.
- ~~**No endpoint registers a `DrillHole`.**~~ **Resolved 2026-08-24** — see
  the Drill hole registration section above.
- **No endpoint deactivates a client** (`Client.is_active`) or amends one
  already registered — nor amends a project or a drill hole once created.
  Out of scope for now: nothing downstream reads `is_active` yet, and no
  workflow has surfaced a need to correct a registered hole's coordinates
  after the fact. Worth revisiting once result entry exists and a wrong
  collar coordinate would actually distort something (a downhole section, a
  composite).
- **`total_depth_m` on `DrillHole` is never checked against the samples
  registered against it.** A sample's `to_depth_m` could exceed the hole's own
  `total_depth_m` and nothing would refuse it. Not addressed here because it
  needs a decision about ordering — does the hole's total depth get entered
  before or after all its samples, and can it be corrected once samples exist
  — that the drill-hole endpoint alone can't resolve.
- **QC insertion policy.** Named in the original roadmap for Phase 2 and
  still open — see "Next actions" above. No `crm_lots`/QC-material schema
  exists yet.
- **`fire_assay_result` is not wired to the crucible it came from.** A
  result's `sample_weight_g` is typed directly into the request and is not
  provably the same portion a crucible was actually charged with — see "Next
  actions" above.
- **No way to correct a batch or crucible charged in error.** The batch
  status machine has no backward move and `Crucible` rows are never deleted
  or amended once created (mutable at the grant level, but nothing in the
  service layer offers an "un-charge" or "cancel a batch" operation). A
  technician who mis-keys a position or charges the wrong sample currently
  has no remedy through the API — only a direct database fix. Not addressed
  because no real workflow has surfaced which correction shape (delete the
  crucible? supersede it? reject and re-charge?) is the right one.
- **No `GET` endpoint lists batches, or lists a sample's crucible history.**
  `GET /api/batches/{id}` requires already knowing the id; there is no
  `GET /api/batches` and no "which batch (if any) is this sample currently
  in" query. Not addressed because nothing downstream has needed it yet —
  the live verification always tracked the id from the `POST` response.
- **Furnace tray geometry (`furnace_rows`/`furnace_columns`) is a single
  global setting**, not per-furnace. A lab with two furnaces of different
  sizes has no way to express that — `Batch` has no `instrument_id` at all
  right now (dropped from the original sketch: `instrument` has no
  registration endpoint yet, and a nullable FK nothing can ever populate
  would have been exactly the half-finished-feature shape this codebase
  avoids). Revisit once instrument registration exists.
