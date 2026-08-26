# MSA LIMS — Progress

**Updated:** 2026-08-25 · **Phase:** 3 complete — a sample now genuinely walks `RECEIVED → IN_PREP → READY_FOR_ASSAY → IN_ASSAY → ASSAYED`, every link a real `check_transition` call rather than a documented bypass. Entering a fire assay result — direct or crucible-linked — now requires the sample to genuinely be `IN_ASSAY`, closing the last of the honest simplifications named since Phase 1. Also done: [AUDIT_AND_BREAKTHROUGHS.md](docs/AUDIT_AND_BREAKTHROUGHS.md)'s idea #7 (Schemathesis fuzzing the live API), which found and fixed two real crashes in its first run, and idea #4 (the furnace tray) — a `GET /api/batches` listing endpoint, a batch detail response enriched with sample/QC labels and furnace geometry, and two React screens rendering the tray as a drawn grid, verified live end to end

New to the codebase? Read [docs/ENGINEERING_GUIDE.md](docs/ENGINEERING_GUIDE.md)
first — it explains the decisions this document only tracks. A full post-Phase-2
audit — gaps, weaknesses, industry research, and a ranked breakthrough agenda
(hash-chained audit anchoring, passkey-bound certificate signing, the furnace
tray UI, QC dossiers, and more) — lives in
[docs/AUDIT_AND_BREAKTHROUGHS.md](docs/AUDIT_AND_BREAKTHROUGHS.md).

---

## Status at a glance

| Phase | Window | State |
|---|---|---|
| 0 · Skeleton | wk 1 | **Done** — domain core, schema, grants, CI gate, UI shell |
| 1 · The spine | wk 2–4 | **Done** — auth, all reference-data registration, submission intake, fire assay result entry, Certificate of Analysis issuance, sample/certificate lookup, and the sample list/detail React screens, all built and verified live |
| 2 · Fire assay batching | wk 5–7 | **Done** — batching, result wiring, per-crucible parting/weighing, and QC insertion (recorded, not enforced), all built and verified live |
| 3 · Lifecycle & prep | wk 8–9 | **Done** — the real prep walk, re-assay, and rejection moves; charging requires genuine `READY_FOR_ASSAY`; fire assay result entry requires genuine `IN_ASSAY`. Every sample-status move in the spine now goes through `check_transition` for real |
| 4 · ICP & bulk import | wk 10–11 | Not started |
| 5 · The Sentinel seam | wk 12–13 | Not started |
| 6 · Ship the story | wk 14–16 | Not started |

**Health:** 501 tests passing (plus a Schemathesis contract-fuzz run kept
separate, see below) · ruff clean · mypy `--strict` clean · migrations
apply from empty and are reversible (Phase 3 needed none — both slices are
service and route changes over the existing schema) · frontend builds and
typechecks clean (TypeScript `strict` + `noUncheckedIndexedAccess`) ·
verified live through curl end to end, Phase 1 and 2's own chain plus: a
soil sample refused the pulp shortcut by name (`"only a pulp may skip
preparation, and this is soil"`, **409**), walked `received → in_prep →
ready_for_assay` across two real `PATCH` calls; a pulp sample reaching
`ready_for_assay` in one; a fresh `RECEIVED` sample refused a crucible
charge with **409** naming the transition itself, then the same sample
charged cleanly once genuinely `ready_for_assay`; a supervisor rejecting a
sample with a reason (**200**) and the identical request with no reason
refused (**422**); `in_assay` named as a target refused at the schema layer
before reaching the service (**422**); and, closing the loop, entering a
fire assay result against that same still-`RECEIVED` sample refused with
**409** (`"a sample cannot go from received to assayed"`) *before* charging,
then succeeding (**201**, `5.000 g/t`) immediately after the identical
sample was genuinely charged into a crucible. A post-Phase-1 audit (see its
own section below) found six defects, all fixed and tested; a five-item
hardening pass followed it.
**Phase 1 — the thinnest complete thread the original roadmap called
for — is done. Phase 2 is done: batching, result wiring, per-crucible
weighing, and recorded-not-enforced QC insertion. Phase 3 is done: every
sample-status move that used to be an honestly-documented bypass — prep,
re-assay, rejection, charging into `IN_ASSAY`, and now entering a result
into `ASSAYED` — genuinely calls `domain.lifecycle.check_transition`.**

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

### Phase 1 audit and hardening — full-codebase review after the phase shipped

A line-by-line audit of everything above — domain, services, web, auth,
migrations, tests, frontend — ran against real Postgres probes, not just the
existing suite (which was already green: the findings below are all things
311 passing tests did not catch). Two groups came out of it.

**Fixed during the audit** (commit `81d7d61`):

1. A zero bead weight with no stated balance sensitivity produced a
   *detected* `0 g/t` grade — exactly the below-detection-vs-zero conflation
   `MeasuredValue` exists to prevent. Now refused at the domain layer;
   stating the sensitivity turns the same reading into a proper non-detect.
2. `MeasuredValue.parse` accepted `"<0"` (a non-detect bounded by nothing)
   and `"-3"` (a negative mass fraction) as storable results. Both refused.
3. Submission intake reported the same unregistered-hole problem once per
   sample rather than once per hole — six samples from one missing hole
   buried any *other* problem under six identical messages. Problems are now
   grouped per hole; a no-project batch names all affected labels on one
   problem.
4. The certificate PDF drew supersession reasons and notes as single
   unwrapped lines that silently ran off the right edge of a signed document,
   and continuation pages had no column headers. Text is now word-wrapped to
   the printable width using exact font metrics, and page breaks re-draw the
   table header — byte-determinism preserved and re-proven.
5. `make seed` referenced a `msa_lims.db.seed` module that did not exist.
   Implemented idempotently, seeding through the same service layer the API
   uses so seeded rows carry honest audit events.
6. README claimed "127 tests" and "Phase 0 complete" — stale since Phase 1.

**Hardening pass immediately following the audit** (same working session):

7. **Label/type compatibility is enforced at intake.** Nothing stopped a
   drill label arriving as `sample_type: soil` or a core sample labelled like
   a stream sediment — a sample whose row contradicts its own identity, which
   would then sit in the hole-interval index while claiming no hole work
   applies. The rule lives pure in `domain/sample_id.py`
   (`label_type_conflict`): CORE/RC_CHIP must parse as drill labels;
   SOIL/STREAM_SEDIMENT/ROCK_CHIP must be surface labels; PULP is allowed
   either shape, because pulp received back from an external lab may carry
   the sender's interval-bearing label.
8. **Submission/certificate numbering survives a race instead of 500-ing.**
   Both allocators were COUNT+1 under a documented single-writer assumption;
   two concurrent requests could both compute the same number and one dies on
   the UNIQUE index with an unhandled IntegrityError. Allocation now retries
   inside a savepoint (`begin_nested`) on a genuine unique violation only
   (SQLSTATE 23505 is retried; every other IntegrityError still propagates),
   recomputing from the live count each attempt. No schema change, and the
   certificate path stays grant-clean — no post-insert UPDATE exists to
   collide with append-only.
9. **The `client` role can no longer read arbitrary lab records.**
   `GET /api/samples`, `/api/samples/{id}`, `/api/certificates/{id}` and
   `/api/certificates/{id}/pdf` were reachable by any authenticated actor,
   including CLIENT — fine for staff-only use, but one forgotten check away
   from exposure if ever fronted directly. There is still no LabUser↔Client
   link to scope rows by, so the honest interim posture is refusal with a
   message saying why: reads require an internal role until per-client
   scoping exists. `GET /api/me` stays open to every authenticated role.
10. **`GET /health`'s database probe left the event loop.** The sync
    SQLAlchemy ping inside an `async def` blocked the loop for the round
    trip; it now runs via `asyncio.to_thread`.
11. **`current_result` is an anti-join, scoped to its sample.** The NOT-IN
    subquery previously scanned every supersession in the whole table for
    every lookup. It is now a correlated LEFT JOIN / IS NULL against an
    aliased successor, letting the planner use indexes instead of materialising
    the exclusion set. Semantics unchanged and re-proven by the existing
    supersession tests plus a new long-chain test.

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

### Result↔crucible wiring — `fire_assay_results/service.py`, `db/models.py`
- **A result may now name the crucible it came from** (`crucible_id`, new
  nullable FK + index, migration `cfef85e840d4`) — closing the gap named in
  open questions since batching landed: the portion weight entered at result
  time was not provably the same one recorded when the crucible was charged.
- **When a crucible is named, the portion is derived, not re-entered.** The
  request carries *either* a crucible *or* a `sample_weight_g` — never both,
  and not neither. A crucible's charge was physically weighed when it was
  charged (that is what `Crucible.sample_weight_g` records); trusting a
  second, freshly-typed number would let a result contradict its own
  provenance. The stored row keeps showing both paths honestly: direct entry
  stores the caller's weight; a wired entry stores the crucible's recorded
  charge.
- **Provenance is validated, not assumed.** A named crucible must belong to
  the same sample the result is for (a result naming another sample's
  crucible is refused, naming both ids), and must have reached at least
  cupellation (`CUPELLED`/`PARTED`/`WEIGHED`) — a bead does not exist before
  cupellation, and a `REJECTED` fusion produces no bead ever. An unknown
  crucible id is a 404, like an unknown sample id: there is no interpretation
  to offer.
- **Result entry still does not advance the crucible's status.**
  `PARTED`/`WEIGHED` are reachable only through their own per-crucible
  measurement paths (see the next section) — typing a bead weight in must
  never silently invent measurements nothing took.
- **Superseding restates the crucible under the identical rule** — no magic
  inheritance from the superseded row, and equally no requirement that a
  correction name one (a correction may legitimately drop a wrong link by
  entering raw weights). Whatever is supplied is validated identically; the
  audit event's `after` payload names the `crucible_id` when there is one.
- **`CrucibleNotFoundError` lives here, not in `batches/service.py`** — same
  cycle-driven reasoning as `SampleNotFoundError`: batches already imports
  from this module, so importing the error back would close a loop for no
  benefit the one-way dependency doesn't already give (decision log).
- **The migration is deliberately schema-only, with no grants companion** —
  Postgres grants are table-level, so `msa_app`'s existing SELECT/INSERT on
  the append-only table already cover the new column. Contrast `b1d0c4e77a10`'s
  rule for new *tables*, where the mutability decision genuinely must be made;
  a column on an already-decided table is not that decision.

### Per-crucible parting and weighing — `batches/service.py`, `domain/batch_lifecycle.py`
- **`PARTED` and `WEIGHED` finally have write paths**, one crucible at a
  time, once cupellation has released them from the tray:
  `POST /api/batches/{id}/crucibles/{cid}/parting` records the lead button,
  prill, and acid volume (`CUPELLED → PARTED`), and
  `POST .../weighing` records the final gold bead (`PARTED → WEIGHED`). Both
  are bench work (`BENCH_ROLES`) — a prep tech parts and weighs; interpreting
  the numbers stays behind `MAY_ENTER_RESULTS`.
- **A status move must carry the measurements that witness it.** Parting is
  refused without a positive button weight, prill weight, and acid volume;
  weighing without the bead. There is no bare flag-flip path to either
  status — an advance with nothing behind it would be a claim about the world
  nobody made. The CHECK constraints mirror the request shape at the database.
- **The two hand-driven moves live in `domain/batch_lifecycle.py`
  (`CRUCIBLE_TRANSITIONS` + `check_crucible_transition`)** as a separate,
  explicitly-partial set from `_BULK_CRUCIBLE_STATUS`: fusion and cupellation
  happen to the whole tray at once through batch transitions, while parting
  and weighing happen to one crucible at a time through these endpoints. The
  checker reuses `TransitionNotAllowedError` — a wrong-stage parting is the
  same kind of refusal as a skipped furnace stage (**409**), not a new error
  vocabulary. `REJECTED` still has no path; nothing invents one.
- **Each measurement is stored once, at the moment of the physical act, and
  never overwritten** — the charge-time discipline continued to the other end
  of the run. Business timestamps (`parted_at`, `weighed_at`) ride along,
  mirroring `charged_at`/`analysed_at`: when it happened is not when it was
  entered.
- **Result entry now reads the weighing back instead of accepting a retyped
  bead.** A result naming a `WEIGHED` crucible derives *both* its numbers —
  portion from the recorded charge, bead from the recorded weighing — and
  refuses typed copies of either; a crucible still only `CUPELLED` or
  `PARTED` takes a typed bead, because nothing on record exists to derive
  from yet (that boundary is stated in the module docstring, not hidden).
  Balance sensitivity remains caller-supplied on both paths, per the open
  question below.
- **URLs nest under the batch** (`/api/batches/{batch_id}/crucibles/{id}/...`)
  and the service refuses a crucible that belongs to some *other* batch with
  a 404 — from this URL, that resource does not exist here.

### QC insertion — `qc_materials/`, `batches/service.py`, `db/models.py`
- **A crucible holds a sample or a QC material — never both, and not
  neither.** `Crucible.sample_id` became nullable and gained a sibling
  `qc_material_id`, with the exclusivity carried by a CHECK constraint at the
  database and by an upfront refusal at the service: the charge request names
  exactly one of the two, the same either/or discipline a fire assay result
  already follows for crucible-vs-raw-weighings. The original `NOT NULL`
  plan was honest for its moment — no QC tables existed, and a nullable column
  nothing could populate would have been a half-built branch — but this pass
  is what that note said it was waiting for.
- **`qc_material` is stock reference data, mutable-tier.** A CRM carries its
  certified gold grade *and* its uncertainty; a blank is defined by having
  neither, and both sides of that rule are enforced together (a CRM without a
  grade is refused naming what's missing; a blank carrying one is refused
  naming the contradiction). The certified unit is fixed to `g/t` rather than
  carried as a column — fire assay grades are `g/t` everywhere in this system,
  and a second unit vocabulary for one column would be two conventions where
  one exists. Lots retire via `is_active`, never deletion; a retired lot is
  refused at charge time with the remedy named (register its replacement).
- **Duplicate-type QC is deliberately not modelled.** `QcMaterialType`'s
  field/prep/pulp duplicates re-insert an *existing sample* — they name a
  sample, not a jar — so registration refuses them ("not a material") and no
  stock row can exist for one. That insertion path is real remaining scope,
  deferred with its reason stated.
- **Insertion is recorded, not enforced** — the decision PROGRESS.md left
  open. A batch may fire without a QC crucible in it; how many controls, of
  which types, is lab QA policy this schema has no basis to invent, and half
  the QC vocabulary (the duplicates) has no insertion path yet, so any
  counting rule written today would guard an incomplete picture. Recording
  honestly is the mechanical prerequisite any future enforcement would need.
- **Everything downstream of the furnace is shared, deliberately.** A QC
  charge gets the same bench-role gate, batch-must-be-`CHARGING` gate,
  position rules, and flux scaling against its weighed-out charge as a sample
  charge; fusion bulk-advances its status with the rest of the tray; parting
  and weighing key on the crucible, not on what was in it. What differs:
  charging moves no sample lifecycle (no sample exists), the audit event
  carries `qc_material_id` instead of `sample_id`, and **a fire assay result
  can never name a QC crucible** — refused at any stage with a message saying
  why. Its bead is judged by QC Sentinel on export (Phase 5); there is
  deliberately no verdict vocabulary here to judge it with.

### Sample lifecycle & prep — `sample_lifecycle/`, `batches/service.py`
- **`sample_lifecycle/service.py`** is where every bare sample-status move
  finally goes through `domain.lifecycle.check_transition` for real —
  closing a gap named honestly in every earlier phase's own docstring.
  `PATCH /api/samples/{id}/status` covers `RECEIVED → IN_PREP`,
  `IN_PREP → READY_FOR_ASSAY`, the pulp shortcut, returning an `ASSAYED`
  sample for re-assay (reason required), and rejection (reason required) —
  the five moves that carry no data beyond the status itself. No new schema:
  `check_transition` and its whole role/reason/pulp-shortcut logic were
  fully built and unit-tested since Phase 0 and simply never had a caller.
- **This is deliberately not a general "set any status" endpoint.**
  `SampleStatusUpdate.target` is a `Literal["in_prep", "ready_for_assay",
  "rejected"]`, not the full `SampleStatus` vocabulary — `in_assay`,
  `assayed`, and `reported` are excluded at the **schema layer**, before a
  request even reaches the service. Each of those three is only ever true
  because of the record that produces it (a crucible, a bead weight, a
  signed PDF); a bare status flip claiming one would be a status "advance"
  with nothing behind it, the exact problem `batches/service.py` already
  names for crucible transitions. A malformed `target` value comes back as a
  clean 422 listing the three legal values, not a runtime service refusal.
- **Charging a crucible now calls the real
  `READY_FOR_ASSAY → IN_ASSAY` transition — not a bypass.** This is the
  payoff `batches/service.py`'s own docstring has been promising since
  Phase 2: "Prep-stage tracking does not exist yet... this is the honest
  reflection of what this system currently tracks." It now does, so charging
  requires it, replacing the old `_NOT_CHARGEABLE` frozenset with a direct
  `check_transition` call. A sample still `RECEIVED` or `IN_PREP` is refused
  with **409** (`TransitionNotAllowedError`), the same status a skipped
  furnace stage gets — a real, deliberate change from the old **422**, and
  a more correct one: `_ERROR_STATUS`'s own comment already says a 409
  means "the sample moved under you," which is exactly what "not ready yet"
  means here.
- **`fire_assay_results/service.py`'s own precondition now also calls the
  real transition** — `IN_ASSAY → ASSAYED`, replacing the Phase 1
  any-non-`REJECTED`-sample bypass. This closes the last honest
  simplification the spine had left open. The check is skipped only when
  the sample already has a current result to point at instead: "supersede
  result #N" is a more useful refusal than "the sample is already assayed"
  for the identical underlying fact, so `_check_supersession`'s existing
  "already has a result" check runs first and the transition check only
  fires when there genuinely is nothing to supersede. Applies identically
  to direct entry and crucible-linked entry — a typed bead against a
  `RECEIVED` sample is refused exactly like a crucible-linked one, because
  the sample's own status is the precondition either way, not the presence
  of a crucible.
- **This is real, wide-reaching scope, done deliberately in its own
  pass.** Requiring genuine `IN_ASSAY` meant every existing test fixture
  that entered a result against a freshly-created sample — across
  `test_fire_assay_results_service.py`, `test_fire_assay_results_api.py`,
  `test_certificates_service.py`, `test_certificates_api.py`, and
  `test_samples_api.py` — had to actually reach `IN_ASSAY` first: either a
  direct ORM status set for service-level tests, or a real
  prep-then-charge-into-a-crucible walk through HTTP for API-level ones.
  Two new fixture helpers (`_charge_into_a_crucible` in the certificates and
  samples API test files) do that walk once and are reused across every
  test in their file that needs it, rather than duplicating the sequence
  per test.

### Contract fuzzing — `tests/integration/test_schemathesis_contract.py`
- **[AUDIT_AND_BREAKTHROUGHS.md](docs/AUDIT_AND_BREAKTHROUGHS.md)'s idea #7,
  "Fuzz the Gates."** Schemathesis derives property-based HTTP fuzzing
  straight from the live app's own `/openapi.json` — no hand-maintained
  fixture data — generating schema-valid *and* adversarial requests for
  every operation and asserting exactly one thing: a request never crashes
  the server. Authenticated as `lab_manager` throughout (the one role every
  write endpoint's gate admits), so the fuzzing exercises each endpoint's
  own request handling rather than re-discovering the role gates the rest
  of the suite already covers directly.
- **Found two real crashes on its first real run — not hypothetical, not
  edge-case theatre.** `POST /api/qc-materials` with
  `certified_au_uncertainty_g_t: 0` reached the database's own CHECK
  constraint directly (`ck_qc_material_certified_au_uncertainty_positive`)
  because `QcMaterialCreate` never mirrored it at the Pydantic layer — every
  sibling numeric field in this API does (`DrillHoleCreate`'s dip/azimuth,
  every reagent on `FluxRecipeCreate`, every weight on the crucible
  schemas), this one pair was simply missed. Separately, any endpoint
  taking an id — path or body — crashed on an integer one past Postgres
  `BIGINT`'s maximum (`9223372036854775808`): syntactically a valid `int`
  Pydantic accepts happily, but Postgres refuses it with a raw
  `DataError` before any query runs. Both are now fixed: `QcMaterialCreate`
  gained `Field(ge=0)`/`Field(gt=0)` matching the CHECK constraints exactly
  (**422**, not a crash), and `web/app.py` gained a global `DataError`
  handler mapping the out-of-range case to **404** — "no row can have this
  id" is functionally identical to "no row has this id," the same answer
  every other `*NotFoundError` in the file already gives, and written fresh
  rather than passed through like the other handlers, since `str(exc)` on a
  raw driver error leaks the query and its bind parameters where every
  other handler's message was written on purpose for a person to read.
- **Each generated example runs inside its own `Session.begin_nested`
  savepoint** — the identical primitive `db/numbering.py` already uses for
  race-safe retries — unconditionally rolled back afterward, not merely
  wrapped in the fixture's one outer transaction like every other
  integration test. The first version of this fixture shared one plain
  transaction across every example for an operation; the first crash it
  found left Postgres refusing all further commands, and every example
  after that one failed with an identical "transaction is aborted" error
  that had nothing to do with what Schemathesis had actually generated,
  burying the one request that mattered under noise. Recovery needed
  `Session.rollback()` specifically — not the narrower
  `SessionTransaction.rollback()` the savepoint object itself returns —
  because a failed flush leaves the ORM Session flagged as needing a full
  rollback, exactly what SQLAlchemy's own error message names as the fix.
- **Scoped to Schemathesis's `not_a_server_error` check alone**, not its
  full default set. `response_schema_conformance` and
  `positive_data_acceptance` both fire constantly here for reasons that are
  not bugs — this API's domain refusals return `{"detail": "<message>"}`
  rather than FastAPI's generic per-field-error array (deliberate, see
  `web/app.py`'s own comment), and this codebase deliberately keeps
  cross-field and stateful business rules out of the Pydantic schema and in
  the service layer, which Schemathesis has no way to know about. Declaring
  accurate per-status response models so schema conformance means something
  here is real, separate scope (see open questions); the crash-detection
  question idea #7 exists to answer needed none of that first.
- **`max_examples=10`, tuned down from an initial 100-per-operation
  default that took over ten minutes wall-clock** — almost entirely
  Hypothesis *shrinking* the two real failures toward a minimal
  reproduction, not fuzzing itself. Once both bugs were fixed, a clean run
  across all 22 operations takes under 30 seconds; a new `pytest.mark.fuzz`
  keeps it out of the plain `pytest -q -m "not fuzz"` step in CI and gives
  it a separately labelled step ("Fuzz the API contract") so a future crash
  is attributable at a glance rather than buried in the main suite's
  output. `make fuzz` runs it alone locally.

### The furnace tray — `batches/service.py`, `web/routes/batches.py`, `frontend/src/pages/BatchList.tsx`, `frontend/src/pages/BatchDetail.tsx`, `frontend/src/components/FurnaceTray.tsx`
- **[AUDIT_AND_BREAKTHROUGHS.md](docs/AUDIT_AND_BREAKTHROUGHS.md)'s idea #4,
  "The Tray."** Finding 20 named the system's most distinctive object as
  invisible — a batch existed only as JSON. This slice builds the visual
  half: a batch list, and a batch detail screen that draws the furnace as a
  grid, one cell per position, coloured by crucible status. The other half
  of the audit's own sketch — charging/parting/weighing as modal forms
  against the existing endpoints — is deliberately deferred; see next
  actions below.
- **`GET /api/batches` closes finding 17's listing gap**, the dependency
  the audit entry itself named. `list_batches` is one query, newest-first,
  a `limit` (default 100) — deliberately as lean as `list_samples`: no
  per-row crucible count, no filters, because nothing downstream asked for
  either yet.
- **A crucible slot's label was invisible over the wire.** `Crucible` has
  only raw `sample_id`/`qc_material_id` FK columns, no ORM relationship to
  either — so `get_batch_detail` was rewritten as one query with two
  `outerjoin`s onto `Sample`/`QcMaterial` (matching `list_samples`'s own
  "explicit joined SELECT, not lazy traversal" precedent) rather than
  sorting `batch.crucibles` and hoping a caller resolves the label itself.
  A new `CrucibleSlot` dataclass (service layer) and `CrucibleSlotOut`
  schema carry the joined label alongside the crucible — `CrucibleOut`
  itself is untouched, still exactly what `charge_crucible`/
  `record_parting`/`record_weighing` return.
- **`CrucibleSlotOut.from_model` takes the ORM `Crucible` plus plain
  label kwargs, not the service-layer `CrucibleSlot` dataclass directly**
  — matches this codebase's standing rule that no schema imports a service
  type; the first draft violated it and was corrected before landing.
- **Furnace geometry (`furnace_rows`/`furnace_columns`) is exposed on
  `BatchDetailOut` for the first time**, sourced from `settings` at the
  route layer — the tray component needs a grid shape to draw, and
  `config.furnace_rows`/`furnace_columns` already existed since Phase 0
  with nothing reading them yet. Still a single lab-wide setting, not
  per-batch or per-instrument — the same open question the config's own
  history already carried, unchanged by this pass (see open questions).
- **A real, unrelated auth gap found and fixed while touching the file:**
  `GET /api/batches/{id}` had no `ActorDep` at all — reachable by anyone,
  authenticated or not. Both it and the new list route now depend on
  `InternalActorDep`, the same "internal role required, `CLIENT` refused"
  gate every other lookup endpoint has carried since the Phase 1 audit.
- **`FurnaceTray`** (new component) renders every position in the grid,
  not just occupied ones — an empty batch still shows the tray it will
  eventually fill, matching how a technician reads the physical one. A
  sample slot links to `/samples/{id}`; a QC slot shows its material type
  as a badge (no sample to link to); the cell's background reuses the
  existing three-tier pill colour system rather than inventing a fourth
  vocabulary for tray state.
- **Poll-free, refresh-on-navigate** — the sketch's own suggestion.
  Websockets are explicitly not built; nothing about this slice's traffic
  pattern (a technician opening a batch to check its state) needs
  push updates.
- 9 new integration tests (5 service-level — batch listing newest-first,
  an empty lab, a `limit`; a sample slot carrying its own label; a QC slot
  carrying its material's name and type — plus 4 over HTTP — the listing
  route, `client` refused with 403 on both routes, an empty list). Existing
  `test_crucibles_are_ordered_by_position` updated for the new
  `CrucibleSlot` wrapper shape.

### Database — `src/msa_lims/db/`
- 15 tables: `client`, `project`, `drill_hole`, `submission`, `sample`,
  `instrument`, `lab_user`, `audit_event`, `fire_assay_result`, `certificate`,
  `certificate_result`, `flux_recipe`, `batch`, `crucible`, `qc_material`.
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
- `cfef85e840d4` — `fire_assay_result.crucible_id` (nullable FK + index),
  schema-only: table-level grants already cover the column, so no grants
  migration accompanies it (see the result↔crucible wiring section above).
- `e8b58da4cd75` — per-crucible measurement columns (`lead_button_weight_mg`,
  `prill_weight_mg`, `parting_acid_volume_ml`, `gold_bead_mg`, and their
  business timestamps) plus CHECK constraints, same schema-only rationale —
  `crucible` is already a mutable-tier table.
- `1d81304e5265` / `b7c2f4a91d08` — the `qc_material` table plus
  `Crucible.qc_material_id` (and `sample_id` going nullable, with the
  exactly-one-of CHECK), then the **mutable** grants for `qc_material`, same
  schema/mutability split as every table before it. The crucible columns ride
  the schema migration with no grants companion — `crucible`'s mutable-tier
  grants are table-level and already cover them.
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
- Fourteen write endpoints, and five read endpoints:
  `GET /api/samples` (the list), `GET /api/samples/{id}` (a sample's current
  result and every certificate that names it), `GET /api/certificates/{id}`
  (metadata, sharing the exact certified-samples query the issuance response
  uses), `GET /api/certificates/{id}/pdf` (the raw document), and
  `GET /api/batches/{id}` (a batch and its crucibles, ordered the way a
  technician reads a tray). `POST /api/fire-assay-results` was the first
  write to compute a response rather than only echo what it stored;
  `POST /api/batches/{id}/crucibles` is the second, computing scaled reagent
  amounts from a recipe rather than storing raw input. The charge endpoint
  names *what* goes into the slot — `sample_id` or `qc_material_id`,
  exactly one, enforced in the service and mirrored by a database CHECK;
  `POST /api/qc-materials` registers the stock those insertions draw from,
  gated by `MAY_CONFIGURE_LAB` like flux recipes. New this phase,
  `PATCH /api/samples/{id}/status` is the first write endpoint whose request
  shape is *narrower* than the domain vocabulary on purpose — a `Literal` of
  three status names, not the full `SampleStatus` enum every other status
  field in this API exposes.

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
  an HTTP status code rather than just success/failure. When the current
  result names its crucible (new this phase), the provenance shows as
  `Crucible: #id` alongside bead and portion; `types.ts`'s `FireAssayResult`
  mirrors the wire format's `crucible_id`.
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
  now also carrying `SampleListItem`, `SampleDetail`, `FireAssayResult`,
  `CertificateReference`, `Batch`, `CrucibleSlot`, and `BatchDetail`,
  hand-written like everything else here with the
  same standing note that these should come from `/openapi.json` once the API
  stops moving.
- **`pages/BatchList.tsx` / `pages/BatchDetail.tsx`** — mirror the sample
  screens' own patterns exactly: the same loading/error/empty states, the
  same 404-vs-generic-error distinction via `ApiError.status`. Batch detail
  adds one new thing neither sample screen needed: a "Furnace tray —
  R×C" section rendering `FurnaceTray`, with a "no crucibles charged yet"
  message when the tray is genuinely empty rather than an empty grid with
  no explanation.

### Tests — 501
- **Unit** (192): units and dimensions, censored values (including the audit's
  new parse refusals — a negative reading and a `<0` limit are rejected, not
  stored), assay arithmetic (including the zero-bead-without-sensitivity
  refusal and its non-detect remedy), sample labels and intervals (including
  the `format_hole_id`/`canonical_hole_id` identity with a parsed sample's own
  `hole_id`, and the label↔type compatibility rule), the sample state machine,
  OIDC token verification against a real self-signed keypair, the auth
  dependency exercised through a real FastAPI app (`TestClient`) across
  dev-header and OIDC modes, Certificate of Analysis PDF byte-determinism
  (identical content twice → identical bytes; different content → different
  bytes; an 80-sample page-break case; a wrapped paragraph-long supersession
  reason and notes block that stays deterministic), the grade-rounding fix
  (a non-terminating division rounds to three decimal places under
  `ROUND_HALF_EVEN`; a clean value is unaffected; a non-detect's detection
  limit is never rounded; the stored full-precision value is untouched by
  rendering) — and, new this phase, flux charge scaling (7 — the exact-nominal-weight identity, doubling and halving the sample weight,
  an unused reagent staying zero, non-positive inputs refused), the batch
  state machine (13 — the full linear walk, every skip and every backward
  move refused, an insufficiently-privileged role refused, `FUSED`/`CUPELLED`
  bulk-mapping to a crucible status and every other transition mapping to
  `None`, and the furnace-position bounds check on all four edges) and the
  hand-driven crucible moves (8 — cupelled→parted and parted→weighed
  accepted, every premature/skipped/backward move refused, no path to
  `REJECTED`).
- **Property** (17, Hypothesis): conversion round-trips within working
  precision; mass conversions exact; substitution always lands within the limit;
  the inverse grade calculation recovers its input; contiguous intervals never
  conflict; generated labels parse back to their parts.
- **Integration** (292, real Postgres): the append-only grants proven against
  the actual application role, now also proving `batch` remains genuinely
  mutable under the same role (11 tests); submission intake against the
  service directly and through the real HTTP app (28 tests — including the
  audit's label↔type refusals and a pre-occupied submission number being
  skipped by the savepoint retry); client and
  project registration (21 tests); drill hole registration (16 tests,
  including the hole-canonicalisation-matches-a-drill-sample proof); fire
  assay result entry (44 tests across the service and HTTP layers — the
  computed grade, the real `IN_ASSAY → ASSAYED` transition (a `RECEIVED` or
  `REJECTED` sample refused with the domain error, not a collected
  validation problem, both service-level and over HTTP as a real **409**),
  every supersession refusal including anti-branching, `current_result`
  answering correctly over a three-link supersession chain as an anti-join,
  and a direct proof that Postgres refuses `UPDATE`/`DELETE` against
  `fire_assay_result`; plus the
  crucible wiring — a named crucible deriving its recorded 45 g charge as the
  stored   portion, re-typing alongside a crucible refused, neither weight nor
  crucible refused, an unknown crucible refused as missing (404), a crucible
  charged with another sample refused naming both ids, pre-cupellation and
  rejected crucibles refused ("a bead exists only after cupellation"), the
  audit event carrying `crucible_id`, a superseding result restating and
  re-deriving from the same crucible, and entry leaving the crucible's own
  status untouched, plus a weighed crucible supplying portion *and* bead to
  the result that names it);
  Certificate of Analysis issuance (26 tests — issuance, the
  ASSAYED→REPORTED transition, every validation refusal including
  cross-client isolation and anti-branching on the amendment chain, a
  pre-occupied certificate number skipped with its PDF re-rendered to match,
  the hash-verified download, and a direct proof that Postgres refuses
  `UPDATE`/`DELETE` against `certificate` too); sample and certificate lookup
  (13 tests — a fresh sample with no result or certificates, the current
  result surfacing after a correction supersedes the original, every
  certificate a sample was ever named on staying listed after an amendment, a
  404 for each unknown id, the `POST`/`GET` responses for one certificate
  asserted byte-for-byte equal, and the `client` role refused with 403 on
  every lookup while internal roles read normally); sample listing (9 tests — an empty lab lists
  nothing, a listed row carries its client and submission but deliberately
  not its grade or certificates, newest-first ordering, `client_id`/`status`
  filtering, an invalid status value refused with 422 before it reaches
  the service, and the `client` role refused); flux recipe registration (9 tests — registration, the role
  gate, a duplicate name refused, a negative reagent amount refused at the
  Pydantic layer, the audit event); furnace batching (32 tests — sequential
  batch numbering, opening restricted to bench roles, charging refused before
  a batch is `CHARGING`, flux scaled correctly onto the stored crucible row,
  a sample already `IN_ASSAY` refused a second charge via the real
  `TransitionNotAllowedError` (not a collected validation problem), a sample
  still `RECEIVED`/`IN_PREP` refused the same way — both service-level and,
  new this phase, over HTTP as a real **409** — an occupied position
  refused, an out-of-tray position refused as the domain error (not a generic
  422), an unknown recipe/batch/sample each refused with the right error
  type, a zero sample weight surfacing the real `FluxCalculationError`, the
  full linear status walk with crucible status bulk-advancing at `FUSED`/
  `CUPELLED`, firing an empty batch refused, skipping a stage refused, batch
  detail ordering crucibles by tray position — both service-level and,
   end-to-end through HTTP, the full open→charge→fire→complete walk and the
   precondition/position/role refusals as real status codes; plus parting and
   weighing (16 tests — parting recording button/prill/
   acid and advancing a cupelled crucible, parting while still charged or
   fused refused as the domain error, parting twice refused, weighing before
   parting refused, a prep tech parting at the bench while a client is
   refused, the transition audit event carrying the measurements, a crucible
   from another batch 404-ing under its URL, and over HTTP the full
   cupelled→parted→weighed walk with every refusal as a real status code);
   and, new this increment, QC insertion (33 tests — material registration
   service-level (11: a CRM registered with its certified grade and audit
   event, a duplicate name refused, an analyst refused, a CRM without its
   grade refused, a blank carrying one refused naming the contradiction, both
   CRM problems reported together, a blank without one registering fine,
   duplicate types refused as "not a material", retirement in place proven
   under the restricted application role, and the database's own CHECK
   refusing a negative certified value) and over HTTP (6 — supervisor `201`,
   analyst `403`, duplicate name, duplicate type, and both sides of the
   certified-grade rule as real status codes); charging a QC crucible (8 —
   flux scaled from its weighed-out charge, sample and QC slots side by side
   in one tray, both-ids and neither-id refused, unknown material 404, a
   retired lot refused with the remedy named, client role refused, the audit
   event carrying `qc_material_id`); the furnace treating a QC slot like any
   other (2 — bulk-advance at `FUSED`, parting and weighing landing the bead
   on the row); result entry refusing a QC crucible at any stage (1, naming
   both ids); and over HTTP (5 — charge `201` with `sample_id` null and
   reagents scaled, both/neither 422, unknown material 404, pending-batch
   422, and the full charge→fire→part→weigh walk for a QC slot)); and, new
   this phase, the sample lifecycle (18 — service-level: the two-step prep
   walk, the pulp shortcut, a non-pulp refused it by name, a client role
   refused starting prep, an unknown sample refused, the transition audit
   event with `before`/`after`, re-assay with a reason, re-assay without one
   refused, rejection with a reason, a prep tech refused rejection, an
   already-`ASSAYED` sample refused rejection naming "amended certificate";
   and over HTTP: the same two-step walk, skipping it refused as **409**, a
   client role refused as **403**, an unknown sample as **404**, `in_assay`
   refused at the schema layer as **422** before reaching the service, and
   rejection with and without a reason)); and, new this pass, the furnace
   tray (9 — batch listing newest-first, an empty lab, a `limit`; a sample
   slot carrying its own label, a QC slot carrying its material's name and
   type; and over HTTP: the listing route, an empty list, and `client`
   refused with **403** on both the list and detail routes).
- **Contract fuzz** (1 collected test, dynamically exercising all 22
  operations × up to 10 generated examples each — not a fixed assertion
  count in the usual sense): Schemathesis-generated schema-valid and
  adversarial requests against the live app, asserting no operation ever
  returns a server error. Found and fixed two real crashes on its first
  run — see "Contract fuzzing" above.
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
own, narrower gate. Demo data was truncated from the dev database afterward.

Result↔crucible wiring verified live over the same kind of curl chain, with
the furnace walk driven for real first: a client, submission, and recipe were
registered; a batch was opened, opened for charging, charged with a `45` g
portion at position `2-3`, and walked to `cupelled`. Entering a result that
named only the crucible (`crucible_id`, **no** `sample_weight_g` in the body)
returned **201** with `sample_weight_g: "45"` — derived from the recorded
charge, not typed anywhere in the request — and `au.value` exactly `"5.000"`
(0.225 mg from exactly 45 g). Every refusal then answered with its real
status code: re-typing a portion alongside the crucible, **422**, "name the
crucible or weigh the portion, not both"; a result against a crucible charged
with a *different* sample, **422** naming both ids ("crucible #1 was charged
with sample #1, not #2"); a result against a still-`CHARGED` (never fired)
crucible, **422**, "a bead exists only after cupellation"; an unknown
crucible id, **404**. Superseding the wired result restated the same
crucible and re-derived the portion (**201**, `5.200 g/t` from a corrected
0.234 mg bead over the same stored 45 g), and `SELECT` on `audit_event`
afterward showed both events — `create` and `amend` — carrying
`crucible_id = 1` in their payloads. The second sample, charged into its own
batch but never fired, stayed `in_assay` throughout, confirming result entry
cannot be tricked into certifying an unfired charge. Demo data was truncated
from the dev database afterward.

Parting and weighing verified live, completing that same chain past
cupellation: a `prep_tech` recorded the parting (**200**, status `parted`,
button/prill/acid echoed back alongside the still-frozen scaled charge),
an `analyst` recorded the weighing (**200**, status `weighed`,
`gold_bead_mg: "0.225"`), and then a result was entered naming *only* the
crucible — no bead, no portion, nothing but `sample_id` and `crucible_id` —
and came back **201** with `gold_bead_mg: "0.225"`, `sample_weight_g: "45"`,
and exactly `5.000 g/t`: every number on the stored row was recorded at the
physical act. The refusals held too: parting a second time, **409**
("cannot go from weighed to parted"); superseding the result while re-typing
the bead, **422** naming the remedy ("its recorded bead is what this assay
produced"); a `client` role attempting to weigh, **403** naming the bench
tier; and `audit_event` showed the full story in order — batch transition,
crucible `cupelled → parted → weighed` with the bead weight in the payload,
then the result's `create`. Demo data was truncated afterward.

QC insertion verified live over a fresh curl chain: a supervisor registered
CRM `OREAS 501d` (**201**, certified grade and uncertainty echoed back); an
analyst registering one was refused (**403**); a blank carrying a certified
value was refused with the contradiction named ("a blank has no certified
grade; leave the certified value and uncertainty unset"); a
`field_duplicate` registration was refused ("not a material; duplicates
re-insert an existing sample"). A batch was opened, opened for charging, and
the CRM charged at 45 g into slot 1-6 (**201**, `sample_id: null`,
`qc_material_id: 1`, every reagent scaled by exactly 45/30 — litharge
60 → `90.0`) beside a client sample charged into slot 2-1; naming both a
sample and the material returned **422** ("exactly one of the two"), as did
naming neither. The batch walked `in_fusion → cupelled`, the QC crucible's
status bulk-advancing with the tray; a `prep_tech` parted it and an analyst
weighed its **0.692 mg** bead onto the row. A fire assay result naming that
QC crucible — even weighed — was refused (**422**, "crucible #1 holds a QC
material (#1), not sample #1"), while the same request naming the *sample's*
crucible derived its recorded charge and bead into exactly `5.000 g/t`,
proving the shared path still composes. `audit_event` showed the QC
crucible's `create` carrying `qc_material_id` and no sample id. Demo data
was truncated afterward.

The sample lifecycle verified live over a fresh curl chain: a client and a
submission with a soil sample and a pulp sample, both `RECEIVED`. Setting the
soil sample straight to `ready_for_assay` was refused (**409**, "only a pulp
may skip preparation, and this is soil"); the pulp sample reached
`ready_for_assay` in one `PATCH` (**200**); the soil sample then walked
`received → in_prep` (**200**) and `in_prep → ready_for_assay` (**200**) in
two. A flux recipe and a batch (opened for charging) were set up, and the
now-`ready_for_assay` soil sample charged into a crucible cleanly (**201**).
A second, freshly-registered soil sample — still `RECEIVED` — was charged
into the same batch and refused with **409**, `"a sample cannot go from
received to in_assay"` — the real transition, not a generic validation
error. That second sample was then rejected by a supervisor with a reason
(**200**, `status: "rejected"`), while rejecting the *first* (now `in_assay`)
sample with no reason was refused (**422**, "rejecting a sample requires a
reason"). Finally, naming `in_assay` as the `target` on the lifecycle
endpoint was refused at the schema layer (**422**, listing `in_prep`,
`ready_for_assay`, and `rejected` as the only legal values) — proving the
exclusion is enforced before any request reaches the service. Demo data was
truncated afterward.

Result entry's own tightened precondition verified live over a fresh curl
chain: a client and a pulp sample, still `RECEIVED`. Entering a fire assay
result against it immediately was refused (**409**, `"a sample cannot go
from received to assayed"`) — before any furnace work happened at all. The
sample was then moved to `ready_for_assay` via the pulp shortcut, a flux
recipe registered, a batch opened and charged with it — and the identical
result request that had just been refused now succeeded (**201**, `au:
{"value": "5.000", ...}`), with no change to the request itself, only to the
sample's genuine status. Demo data was truncated afterward.

The two fuzz-found fixes verified live directly: `POST /api/qc-materials`
with `certified_au_uncertainty_g_t: "0"` on a CRM now returns **422** naming
the field (`"Input should be greater than 0"`) instead of crashing into the
database's CHECK constraint; `GET /api/samples/9223372036854775808` (one
past Postgres `BIGINT`'s maximum) now returns **404**
(`"no resource with that id exists"`) instead of a raw `DataError`; and a
negative `certified_au_value_g_t` is refused the identical way (**422**,
`"Input should be greater than or equal to 0"`). No demo data to truncate —
every one of these requests was correctly refused before anything wrote to
the database.

The furnace tray verified live through the browser: a client, three pulp
samples, a "Tray Demo" flux recipe, a CRM (`OREAS 501d`), and a blank
(`Silica Blank`) were registered through the real API, then a batch was
opened, charged with three samples and both QC materials across a 6×6
tray, and walked to `fused`. `/batches` rendered a table with
`BATCH-2026-0001` and a correctly amber `FUSED` pill; clicking through to
`/batches/1` rendered the tray as a real 6×6 grid — five occupied cells
(three sample labels, a bold `CRM` badge, a bold `BLANK` badge) all
amber-tinted for `fused`, every other cell dashed with a centred "—", with
zero console errors. Clicking a sample's label inside its tray cell
navigated to `/samples/{id}` and rendered that sample's detail page
correctly; the "← All batches" and "← All samples" back-links both
returned to their respective lists. Demo data was truncated from both the
dev database and, separately, the `msa_test` database — which had
accumulated 54 leftover `batch` rows from an unrelated Schemathesis fuzz
run between sessions, discovered when a fresh "empty lab" listing test
unexpectedly returned non-empty.

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
| 2026-08-25 | A zero bead weight is **refused** unless `balance_sensitivity_mg` is stated (found in audit) | A bare zero cannot be told apart from a reading below what the balance resolves. Reporting it as detected `0 g/t` flattens the exact distinction `MeasuredValue` exists to keep; stating the sensitivity converts the identical reading into a non-detect at the grade it corresponds to. The refusal names the remedy rather than guessing a convention. |
| 2026-08-25 | Sample labels and sample types are **cross-checked at intake** (found in audit) | A drill label arriving as `soil` produces a row that contradicts its own identity — interval columns populated, hole resolved, while claiming a medium that has neither. CORE/RC_CHIP must parse as drill labels; SOIL/STREAM_SEDIMENT/ROCK_CHIP must be surface; PULP takes either shape because externally-received pulp may carry its sender's interval label. Pure function in `domain/sample_id.py`, so the rule is testable without a session like every other intake rule. |
| 2026-08-25 | Sequential document numbers **retry on unique violation inside a savepoint** rather than trusting COUNT+1 alone (audit hardening) | COUNT+1 is correct only under single-writer; two concurrent requests could both compute the same number and one would 500 on the UNIQUE index. Retrying in `begin_nested()` recomputes from the live count with no schema change and no sequence to grant; only SQLSTATE 23505 retries, so real constraint bugs still surface loudly. Post-insert UPDATE was never an option: `certificate` holds no UPDATE grant, append-only by design. |
| 2026-08-25 | Lookup endpoints require an **internal role** until per-client row scoping exists (audit hardening) | Any authenticated actor — including CLIENT — could read any sample or certificate by id. With no LabUser↔Client link to scope rows by, the honest interim posture is refusal with a message naming why, not open access justified by "nobody malicious uses the demo." `GET /api/me` stays reachable by every authenticated role; it is how you find out what you are. |
| 2026-08-25 | A result naming its crucible **derives** the portion from the crucible's recorded charge; the request may carry one or the other, never both | The charge was physically weighed when the crucible was charged — that is what `Crucible.sample_weight_g` records. Accepting a second, freshly-typed weight would let a result contradict its own provenance, which is precisely the gap this wiring exists to close. Neither supplied is equally refused: silence is not a measurement. |
| 2026-08-25 | A named crucible must have reached at least **cupellation**, and a rejected fusion never qualifies | A gold bead does not exist before cupellation — accepting `gold_bead_mg` against a merely-charged crucible would record a weighing nothing performed. The allowed set (`CUPELLED`/`PARTED`/`WEIGHED`) reads as physical possibility, not administrative progress. |
| 2026-08-25 | Result entry **does not advance** the crucible's status | Parting and weighing are per-crucible acts recorded at the bench by their own write paths, with their own measurements. Auto-setting `WEIGHED` from a typed bead weight would invent measurements nobody took — and would arrive before the real ones can be recorded. |
| 2026-08-25 | `CrucibleNotFoundError` lives in `fire_assay_results/service.py`, not `batches/service.py`, despite batches owning the entity | Same cycle-driven reasoning as `SampleNotFoundError` staying put: `batches/service.py` already imports from `fire_assay_results/service.py` (it raises `SampleNotFoundError` while charging), so importing the error back would close an import loop for no benefit the one-way dependency doesn't already give. One canonical class either way. |
| 2026-08-25 | The crucible-link migration (`cfef85e840d4`) is **schema-only, no grants companion** | Postgres grants are table-level; `msa_app`'s existing SELECT/INSERT on the append-only `fire_assay_result` already cover the new column. Contrast `b1d0c4e77a10`'s rule for new *tables* — there the mutability decision genuinely must be made; adding a column to an already-decided table is not that decision. |
| 2026-08-25 | A crucible status move **carries the measurements that witness it** — parting without a button/prill/acid or weighing without a bead is refused | A status advance with nothing behind it would be a claim about the world nobody made — the same reasoning that refuses a nullable FK nothing can populate. The database CHECKs mirror the rule, so the service-layer promise is also a schema-enforced one. |
| 2026-08-25 | Hand-driven crucible moves are a **separate, explicitly-partial transition set**, not a second full state machine | Fusion and cupellation happen to the whole tray at once through batch transitions; parting and weighing happen to one crucible at a time through their endpoints. Modelling both kinds in one table would imply direct paths (say, `CHARGED → PARTED`) that no endpoint offers. The checker reuses `TransitionNotAllowedError`, so a wrong-stage parting is the same refusal family as a skipped furnace stage. |
| 2026-08-25 | Parting and weighing are **bench work** (`BENCH_ROLES`), not result interpretation | Physical acts at the furnace and balance are the same kind of authority as charging — which a prep tech holds. Interpreting what was weighed stays behind `MAY_ENTER_RESULTS`; recording that it happened does not. |
| 2026-08-25 | A result naming a **weighed** crucible derives its bead from the recorded weighing; before weighing, a typed bead remains honest input | Once the crucible's own weighing exists, accepting a second typed number would let the result contradict its provenance — identical to the portion rule. But until then there is genuinely nothing to derive from: refusing the typed bead would block every real assay, so the boundary is stated rather than papered over. |
| 2026-08-25 | A crucible charge names **exactly one** of `sample_id`/`qc_material_id` — both and neither refused, the exclusivity also carried by a database CHECK | The fire-assay-result request already established this shape ("either a crucible or raw weighings — never both, and not neither"). One slot holds one thing; a request naming two would be ambiguous about what was assayed, and naming zero would be an empty crucible pretending to be charged. |
| 2026-08-25 | QC insertion is **recorded, not enforced** — a batch may fire with no QC crucible in it (the open Phase 2 question, decided) | How many controls a batch needs, of which types, is lab QA policy this schema has no basis to invent — and half the QC vocabulary (`field`/`prep`/`pulp` duplicate insertions re-insert an existing *sample*) has no insertion path yet, so any counting rule written today would guard half the picture. Recording honestly is the mechanical prerequisite any future enforcement would need; the policy itself stays with the lab. |
| 2026-08-25 | Duplicate-type QC is **refused at registration**, not given nullable stock rows | A field duplicate has no jar, no lot number, no certified grade — it is a *re-insertion of an existing sample*. Giving it a row shaped like stock would make `qc_material` lie about what it holds; the honest refusal names what duplicates actually are and defers their real insertion path. |
| 2026-08-25 | A **retired** QC material cannot be charged into a new batch | Retirement means "this lot no longer guards work" — an expired or contaminated CRM inserted anyway would produce QC data everyone would have to remember to discount. The refusal names the remedy (register its replacement); historical batches still name the retired row, which is why retirement is `is_active`, never deletion. |
| 2026-08-25 | A fire assay result can **never name a QC crucible**, at any stage | Result entry exists for samples; a QC insertion holds none. Its bead is judged by QC Sentinel on export (Phase 5), and there is deliberately no verdict vocabulary here to judge it with — letting one through would start this system silently grading its own controls under a sample's identity. |
| 2026-08-25 | `PATCH /api/samples/{id}/status` accepts a **`Literal` of three status names**, not the full `SampleStatus` enum | `in_assay`, `assayed`, and `reported` are each true only because of a record that produced them (a crucible, a bead weight, a signed PDF). A generic "set any status" endpoint would let one of those be claimed with nothing behind it — refusing the other three at the schema layer, before any request reaches the service, makes the exclusion self-documenting in the OpenAPI contract rather than a runtime special case. |
| 2026-08-25 | Charging a crucible now **genuinely calls** `check_transition` for `READY_FOR_ASSAY → IN_ASSAY`, replacing the `_NOT_CHARGEABLE` bypass | The bypass was honestly documented as temporary since Phase 2: prep tracking didn't exist, so nothing could legitimately reach `READY_FOR_ASSAY`. It does now. A sample not yet ready is refused with the real `TransitionNotAllowedError` (**409**) instead of a collected validation problem (**422**) — a deliberate status-code change, and the more correct one per `_ERROR_STATUS`'s own comment ("409 means the sample moved under you"). |
| 2026-08-25 | `fire_assay_results/service.py`'s own precondition (any non-`REJECTED` sample) is **left untightened** in this pass | Requiring the real `IN_ASSAY → ASSAYED` transition too would be correct, but it ripples into every certificate and sample test fixture that enters a result against a freshly-created sample — a separately-scoped decision, not squeezed into the phase's first slice to keep this one coherent and reviewable on its own. |
| 2026-08-25 | `fire_assay_results/service.py`'s precondition **now also calls** the real `IN_ASSAY → ASSAYED` transition, closing the deferral above | The ripple this required — every fixture across five test files that entered a result against a freshly-created sample now walks it to `IN_ASSAY` first — was real, but doing it in a dedicated pass kept each change reviewable on its own rather than bundled into an already-large first slice. |
| 2026-08-25 | The `IN_ASSAY → ASSAYED` check is **skipped when the sample already has a current result**, not run unconditionally | A second sample already carrying a result is always also not `IN_ASSAY` (it moved to `ASSAYED` when the first result landed), so the two checks would always both fire together for that case. "Supersede result #N" names the actual remedy; "the sample is already assayed" (`check_transition`'s generic message) does not — so the more specific, already-existing check runs first and the transition check only fires when there is genuinely nothing to point at instead. |
| 2026-08-25 | Contract fuzzing (audit idea #7) is **scoped to Schemathesis's `not_a_server_error` check alone** | `response_schema_conformance` and `positive_data_acceptance` both fire constantly for reasons that are not bugs here — domain refusals return `{"detail": string}` by design, and cross-field/stateful business rules live in the service layer on purpose, both invisible to a schema Schemathesis reads literally. The crash-detection question idea #7 exists to answer needs neither; declaring accurate response models so schema conformance means something is real, separate scope. |
| 2026-08-25 | An out-of-range id (past Postgres `BIGINT`'s max) is mapped to **404 globally via a `DataError` handler**, not fixed per-endpoint | The bug is systemic — every `int`/`int \| None` id field across the API shares it, path and body alike — so a single global handler closes the whole class in one place rather than adding an upper `Field` bound to a dozen schema classes individually. "No row can have this id" and "no row has this id" are the same fact to a caller; both get the same status every other `*NotFoundError` already uses. |
| 2026-08-25 | The `DataError` handler's message is **written fresh**, not passed through via `str(exc)` like every other handler in the dict-driven loop | Every other handler's exception was raised on purpose by application code with a message written for the person who hit it. A raw driver `DataError` carries the failed SQL and its bind parameters — fine for a log, not for a client response. |
| 2026-08-25 | Each fuzz-generated example runs in its **own `Session.begin_nested` savepoint**, not the fixture's one shared transaction every other integration test uses | A raw, uncaught database error (exactly the class of bug fuzzing exists to find) leaves Postgres refusing further commands until a rollback. One shared transaction across many generated examples let the first such crash poison every example after it with an identical, unrelated "transaction is aborted" failure — the savepoint makes each generated example as independent as it would be as a real, separate request. |
| 2026-08-25 | `max_examples` tuned down to **10**, from the ~100-per-operation default that took over ten minutes | Almost all of that time was Hypothesis *shrinking* the two real failures toward a minimal reproduction, not fuzzing breadth. A clean run (no failures to shrink) finishes in under 30 seconds at this setting — fast enough for a routine CI step, the "effort S, one afternoon" scope idea #7 was scoped at. |
| 2026-08-25 | The tray's first slice covers **listing and visualization only** — charging/parting/weighing as modal forms is deferred to a separate pass | The audit's own sketch named both halves in one entry, but this codebase's standing discipline is one coherent, reviewable vertical slice per session. The read side (list + detail + tray render) is independently valuable and ships alone; the write-side forms reuse endpoints that already exist and need no schema change, so nothing about deferring them blocks anything else. |
| 2026-08-25 | A crucible slot's label is resolved via an **explicit joined `SELECT`** in `get_batch_detail`, not an ORM `relationship()` added to `Crucible` | `Crucible.sample_id`/`qc_material_id` were deliberately left as raw FK columns with no relationship when QC insertion landed — adding one now only for this read would be a schema change to serve a single query. The joined-`SELECT` style already used by `list_samples` answers the same "who does this row point at" question without touching the model. |
| 2026-08-25 | `CrucibleSlotOut.from_model` takes the ORM `Crucible` plus plain label kwargs, not the service's `CrucibleSlot` dataclass | Every other schema's `from_model` takes an ORM object plus plain kwargs; none imports a service-layer type. A first draft took the dataclass directly and was corrected before landing — accepting it would have been the first schemas→service import in the codebase, for no benefit the kwargs form doesn't already give. |
| 2026-08-25 | `GET /api/batches/{id}` gained `InternalActorDep`, a gap found while adding the sibling list route, not a planned part of this slice | The route had no auth dependency at all — reachable by any caller, unauthenticated or not, the exact hole the Phase 1 audit closed on every other lookup endpoint. Fixing it here, while the file was already open for the list route, matches this codebase's "fix what you find, name it" discipline rather than filing it as a future item. |
| 2026-08-25 | Furnace geometry (`furnace_rows`/`furnace_columns`) is exposed on the **existing** `BatchDetailOut`, not a new settings-reading endpoint | The tray component needs a grid shape to draw and nothing else currently needs the raw config value on its own. Adding a dedicated `GET /api/config` (or similar) for one consumer would be API surface built ahead of a second use case; the value already lives in `settings` and costs nothing extra to attach to a response the tray screen was already fetching. |

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
2. ~~**QC insertion policy.**~~ **Done 2026-08-25** — `qc_material` stock
   table (CRM/blank/coarse blank, certified grade + uncertainty for CRMs,
   mutable-tier grants), `Crucible.qc_material_id` with the exactly-one-of
   CHECK (`sample_id` now nullable), charging through the same endpoint,
   furnace/bench paths shared, and a fire assay result refused from ever
   naming a QC crucible. Insertion is recorded, not enforced — see the
   decision log. Duplicate-type QC (re-inserting an existing sample) remains
   future scope. 33 new tests; verified live end to end — see "Verified
   live." **Phase 2 is complete.**
3. ~~**Wire `fire_assay_result` to the crucible it came from.**~~ **Done
   2026-08-25** — nullable `crucible_id` on the result (migration
   `cfef85e840d4`, schema-only); a named crucible derives the stored portion
   from its recorded charge and refuses re-typed weights; provenance checks
   (same sample only, cupellation reached); supersession restates the same
   rule. 15 new tests; verified live end to end including every refusal as a
   real status code — see "Verified live."
4. ~~**Per-crucible weighing (`lead_button_weight_mg`, `prill_weight_mg`,
   `parting_acid_volume_ml`) after cupellation.**~~ **Done 2026-08-25** —
   nullable measurement columns on `Crucible` (migration `e8b58da4cd75`),
   `CRUCIBLE_TRANSITIONS` in `domain/batch_lifecycle.py`, and two write paths
   under the batch URL: parting (`CUPELLED → PARTED`, button + prill + acid)
   and weighing (`PARTED → WEIGHED`, bead). A result naming a weighed
   crucible now derives both its numbers from those records. 30 new tests;
   verified live end to end — see "Verified live." **Phase 2's last open
   item is the QC insertion policy.**

## Next actions (Phase 3 — lifecycle & prep)

1. ~~**The real prep walk (`RECEIVED → IN_PREP → READY_FOR_ASSAY`), re-assay,
   and rejection.**~~ **Done 2026-08-25** — `sample_lifecycle/service.py`,
   `PATCH /api/samples/{id}/status`, restricted at the schema layer to the
   three bare-flip targets (`in_prep`, `ready_for_assay`, `rejected`).
   Charging a crucible now calls the real `check_transition` for
   `READY_FOR_ASSAY → IN_ASSAY` instead of the Phase 1/2 bypass, closing a
   gap named in both phases' own docstrings. 20 new tests; verified live end
   to end — see "Verified live."
2. ~~**Tighten `fire_assay_results/service.py`'s own precondition to require
   the real `IN_ASSAY → ASSAYED` transition**~~, replacing its former
   any-non-`REJECTED`-sample guard. **Done 2026-08-25** — a `RECEIVED`,
   `IN_PREP`, `READY_FOR_ASSAY`, or `REJECTED` sample is now refused with a
   real `TransitionNotAllowedError` (**409**), whether entered directly or
   with a named crucible; the check is skipped when the sample already has a
   current result, so "supersede result #N" still surfaces instead of the
   generic "already assayed." Every fixture across
   `test_fire_assay_results_service.py`, `test_fire_assay_results_api.py`,
   `test_certificates_service.py`, `test_certificates_api.py`, and
   `test_samples_api.py` that entered a result against a freshly-created
   sample now walks it to `IN_ASSAY` first — a direct ORM status set for
   service-level tests, a real prep-then-charge HTTP walk for API-level
   ones. 2 new tests; verified live end to end — see "Verified live." **Every
   sample-status move in the spine now goes through a real
   `check_transition` call; Phase 3 is complete.**
3. **A `PrepRecord` of what physically happened during prep** — crushing,
   splitting, pulverising, which instrument, the resulting pulp weight — is
   real remaining scope this pass deliberately did not build. Bare status
   flips are honest for what this system currently tracks (matching
   `Crucible`'s "no half-finished branch" discipline), but a real
   contamination investigation would eventually want to trace a pulp back to
   the pulveriser that touched it, the way a bead can already be traced to
   its crucible. No `instrument_id` link exists on any prep record yet
   because no prep record exists yet.

## Next actions (post-Phase-3 — audit-driven)

1. ~~**[AUDIT_AND_BREAKTHROUGHS.md](docs/AUDIT_AND_BREAKTHROUGHS.md)'s idea
   #7, "Fuzz the Gates."**~~ **Done 2026-08-25** — Schemathesis contract
   fuzzing wired to `pytest.mark.fuzz` and CI; found and fixed two real
   crashes on its first run. See "Contract fuzzing" above.
2. ~~**Idea #4, "The Tray" — listing and visualization.**~~ **Done
   2026-08-25** — `GET /api/batches`, an enriched `GET /api/batches/{id}`,
   `BatchList`/`BatchDetail`/`FurnaceTray`. 9 new tests; verified live end
   to end — see "Verified live." **Charging/parting/weighing as modal
   forms against the existing endpoints — the audit sketch's other half —
   is real remaining scope, deliberately deferred to its own pass.**
3. **Idea #18, generated TypeScript types from `/openapi.json`**, remains
   unbuilt. `types.ts` gained `Batch`/`CrucibleSlot`/`BatchDetail`
   hand-written this pass, under the same standing note every other type
   in the file already carries.

## Open questions

- **The OpenAPI schema declares no accurate per-status response models for
  domain refusals**, so Schemathesis's `response_schema_conformance` and
  `positive_data_acceptance` checks cannot run meaningfully yet — see the
  "Contract fuzzing" section and its decision-log rows. Declaring real
  `responses={422: {...}, 404: {...}, ...}` models per route (or a shared
  envelope type FastAPI can reuse) would let those checks graduate from
  "always noisy, deliberately excluded" to actually enforced — the natural
  next increment of audit idea #7, not attempted in this pass.
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
- **No per-client row scoping on reads — the `client` role is refused
  outright meanwhile.** The audit's interim hardening: since there is no
  LabUser↔Client link to scope rows by, `GET /api/samples`,
  `/api/samples/{id}`, `/api/certificates/{id}` and `/api/certificates/{id}/pdf`
  now require an internal role and answer the external role with **403** and a
  message naming why (see `web/deps.py`'s `internal_actor`). That closes the
  "any authenticated actor can read any grade by id" hole without pretending
  scoping exists. A real client portal still needs the schema work: a durable
  LabUser↔Client association, then replacing `internal_actor` with a
  row-scoped dependency on exactly these endpoints.
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
- ~~**QC insertion policy.**~~ **Resolved 2026-08-25** — insertion is
  recorded, not enforced; see the QC insertion section and the decision log.
  Still open within it: the duplicate-type insertion path (re-inserting an
  existing sample) has no schema or endpoint yet.
- ~~**`fire_assay_result` is not wired to the crucible it came from.**~~
  **Resolved 2026-08-25** — a result may name its crucible, and when it does
  the stored portion *is* the recorded charge, not a re-typed number; see
  "Result↔crucible wiring" above and the decision log.
- ~~**`fire_assay_results/service.py` still accepts a result for any
  non-`REJECTED` sample.**~~ **Resolved 2026-08-25** — it now requires the
  real `IN_ASSAY → ASSAYED` transition, same as charging requires
  `READY_FOR_ASSAY → IN_ASSAY`; see "Next actions" above and the decision
  log.
- **No `PrepRecord` of what physically happened during prep.** A bare status
  flip is the whole fact `sample_lifecycle/service.py` currently tracks — no
  instrument, no pulp weight, no operation type (crush/split/pulverise). See
  "Next actions" above.
- **A sample can be charged into a crucible without ever having gone through
  a formal receipt inspection or weighing check against `weight_received_g`.**
  Nothing compares a prepped pulp's implied weight to what was logged at
  intake; a large, unexplained mass loss during prep would go unnoticed.
- **A wired result's crucible is not yet shown beyond an id.**
  `FireAssayResultOut.crucible_id` and the sample-detail screen surface the
  link as `#id`; naming which batch and tray position that crucible occupied
  needs either a relationship read or a batch-listing endpoint (also still
  missing — below). Deferred until a screen actually needs to render it.
- **No way to correct a batch or crucible charged in error.** The batch
  status machine has no backward move and `Crucible` rows are never deleted
  or amended once created (mutable at the grant level, but nothing in the
  service layer offers an "un-charge" or "cancel a batch" operation). A
  technician who mis-keys a position or charges the wrong sample currently
  has no remedy through the API — only a direct database fix. Not addressed
  because no real workflow has surfaced which correction shape (delete the
  crucible? supersede it? reject and re-charge?) is the right one.
- ~~**No `GET` endpoint lists batches.**~~ **Resolved 2026-08-25** —
  `GET /api/batches` exists now, newest-first with a `limit`; see "The
  furnace tray" above. Still missing: "which batch (if any) is this sample
  currently in" — a sample's detail view has no reverse link to its
  crucible/batch, only the crucible's own id via its fire assay result.
- **Furnace tray geometry (`furnace_rows`/`furnace_columns`) is a single
  global setting**, not per-furnace. A lab with two furnaces of different
  sizes has no way to express that — `Batch` has no `instrument_id` at all
  right now (dropped from the original sketch: `instrument` has no
  registration endpoint yet, and a nullable FK nothing can ever populate
  would have been exactly the half-finished-feature shape this codebase
  avoids). Revisit once instrument registration exists. Now surfaced
  read-only on `BatchDetailOut` and rendered by `FurnaceTray` — the tray
  UI inherits this exact limitation: every batch draws the same
  lab-wide grid regardless of which furnace actually fired it.
- **The tray has no write paths of its own.** Charging, parting, and
  weighing all still require curl or a future modal form — `BatchDetail`
  is read-only. The audit's own sketch names this as the natural next
  increment; see "Next actions" above.
