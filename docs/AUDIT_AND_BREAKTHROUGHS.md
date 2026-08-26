# MSA LIMS — Full Audit & Breakthrough Agenda

**Written:** 2026-08-25 · **Scope:** the entire codebase as of Phase 2 completion
(470 tests, 15 tables, 13 write + 5 read endpoints, 3 React screens)

This document does two jobs. **Part I–II** are an honest audit: what the system
covers, where it is weak, and what would hurt first in a real lab or a real
accreditation assessment. **Part III** is research into what the industry and
the standards world actually do. **Part IV** is what to do about it: a ranked
set of ideas worth building, several of which aim past "catch up to commercial
LIMS" toward things almost no LIMS does.

Nothing here re-litigates settled decisions — those live in PROGRESS.md's
decision log with their reasons. This document is the *forward* half.

---

## Part I — Where the system stands

A one-paragraph inventory, so the gaps below have a floor to stand on:

**Domain** (`domain/`, pure, no I/O): exact-rational units with a mass
dimension; censored `MeasuredValue` (no `__float__`, substitution by name);
gravimetric grade calculation with non-detect-at-sensitivity semantics and the
exact assay-ton identity; drill/surface label grammar with half-open intervals
and label↔type compatibility; sample state machine; linear batch machine plus
partial per-crucible moves; Decimal flux scaling.

**Services**: submission intake (whole-batch validation), client/project/drill-
hole registration, fire assay result entry (append-only, supersession chains,
crucible provenance with derived-not-typed weights), certificate issuance
(byte-deterministic PDF, frozen result references, hash-verified download),
sample/certificate lookup, furnace batching (position constraints, scaled
charges, bulk crucible advance), per-crucible parting/weighing, QC material
stock registration and insertion (recorded, not judged).

**Trust posture**: append-only by Postgres grant on results/certificates/audit;
restricted application role distinct from migration owner; auth with no
verification-off switch; role tiers as named constants; every write audited
per-row; numbering race-safe via savepoint retry.

**Tests**: 470 — 192 unit, 17 property (Hypothesis), 261 integration against
real Postgres *as the restricted role*, including direct proofs that the
database itself refuses tampering. CI runs lint, strict types, migrations from
empty, downgrade-to-base round trip, full suite, frontend build+typecheck.

That is the strong part. Now the other part.

---

## Part II — Audit findings

Graded by kind: 🔴 integrity/compliance risk · 🟠 security · 🟡 completeness
against real lab practice · ⚪ engineering debt. Effort is S/M/L.

### A. Integrity & correctness

1. 🔴 **The audit trail is append-only but not tamper-*evident*.** Grants stop
   `msa_app` from UPDATE/DELETE, which is excellent — against the application.
   The schema owner can still rewrite history silently, and nothing detects it.
   A row edited in place leaves no seam. Every claim "append-only by grant"
   makes is one `psql` connection away from being unfalsifiable. *(Fix shape:
   idea #1 — hash chain. Effort M.)*
2. 🔴 **A superseded result silently stales its certificates.** Known open
   question, restated here as a finding because it is a correctness gap with a
   compliance face: correct an assay after issuance and every certificate that
   froze the old row keeps circulating with nothing flagging it — no badge, no
   list, no letter. ISO 17025 assessors ask exactly this ("how do you notify
   recipients of materially amended reports?"). *(Effort M: a staleness view +
   a line on the PDF + an advisory endpoint.)*
3. 🟡 **`created_at` columns are naive timestamps.** Business timestamps
   (`analysed_at`, `issued_at`, …) are correctly `DateTime(timezone=True)`,
   but the mixin's `created_at` uses bare `DateTime()` with `server_default
   now()` — a server-timezone-dependent instant on a column meant to be the
   objective record of when a row appeared. One migration fixes it forever;
   after scale, it is a data archaeology project. *(Effort S.)*
4. 🟡 **No idempotency protection on writes.** A retried POST (flaky mobile
   network, double-clicked button, replayed webhook someday) creates a second
   submission/batch/result. Numbering retries survive races between *distinct*
   requests but nothing distinguishes a retry from a new request. *(Effort M:
   `Idempotency-Key` header + a keyed request table.)*
5. 🟡 **No optimistic concurrency on mutable rows.** Two supervisors editing
   one flux recipe last-write-win silently. Low stakes today (recipes barely
   change), higher when instrument calibration and QC limits land. *(Effort S:
   ETag/`If-Match` on PATCH.)*
6. ⚪ **Sequential document numbers still encode COUNT.** The savepoint retry
   made them race-safe, not meaningful. A gap in the sequence (from any
   rollback) looks like a missing certificate to an auditor without a
   documented explanation. Cheap fix: a one-line note in the seed/SOP docs, or
   go further with idea #1's monotonic ledger.

### B. Security

7. 🟠 **No rate limiting, no security headers, no CORS hardening.** The API has
   zero middleware for throttling, HSTS, CSP, or cache control; the Vite proxy
   means dev never exercises CORS at all. Fine behind a LAN demo; the moment
   this deploys, login-less endpoints (`/health`, `/docs`) and header-auth
   brute force are unthrottled. *(Effort S-M: slowapi or a tiny token bucket,
   secure headers middleware, explicit CORSMiddleware allowlist.)*
8. 🟠 **The React app authenticates as nobody.** It relies on dev-header
   mode's default analyst. No login screen, no token storage, no OIDC code
   flow — meaning *nothing user-facing forces the auth design*, and the day it
   must, it will be built under deadline pressure. The signing ceremony (idea
   #2) is the natural forcing function.
9. 🟠 **Reads are authorised but unaudited.** Every write lands in
   `audit_event`; nobody records who *viewed* a grade or downloaded a PDF.
   For client-portal work (who saw whose results?) a read trail is the thing
   an assessor or a dispute asks for. *(Effort M — and deliberately sampled,
   or the table becomes the hottest table in the database.)*
10. ⚪ **Dependency & container scanning absent.** No pip-audit/`npm audit`/
    Dependabot in CI; the app ships no Dockerfile at all (only Postgres is
    composed). *(Effort S each.)*

### C. Completeness against real lab practice

11. 🟡 **Prep stages don't exist, so the sample lifecycle lies by omission.**
    Phase 3 scope, already planned — flagged here because *everything*
    downstream (charging's lifecycle bypass, `ASSAYED` jumps) is documented as
    waiting on it. It is the single largest honesty debt in the domain model.
12. 🟡 **Instruments cannot be registered**, so balance sensitivity stays a
    caller-supplied guess (open question), furnace geometry is one global
    grid, and no result can trace to the balance that weighed its bead — an
    ISO 17025 clause 6.4 requirement (equipment records and calibration
    traceability). *(Absorbed by idea #6.)*
13. 🟡 **Silver doesn't exist yet.** `silver_by_difference` is computable from
    the prill weighing already stored at parting time, but there are no Ag
    columns, no decision recorded about reporting silver routinely vs on
    request, and certificates are gold-only. Real fire assay labs quote Au-Ag
    together. *(Effort M; pairs naturally with Phase 4's ICP scope.)*
14. 🟡 **Duplicate-type QC insertions have no path.** Field/prep/pulp
    duplicates re-insert an existing sample; registration honestly refuses
    them today. Until they exist, Thompson–Howarth precision analysis (the
    industry's standard duplicate-pair method — see Part III) has no input
    data, and QC coverage is half the vocabulary. *(Effort M.)*
15. 🟡 **No barcode/QR labels, no label printer story.** Physical sample
    custody runs on barcodes in every real lab (Datamine's LIMS sells it as a
    headline feature). The system generates beautiful numbers nobody printed
    onto a bag. *(Effort S-M: ZPL template + a labels endpoint.)*
16. ⚪ Reference-data lifecycle holes (already catalogued): no deactivate/
    amend endpoints for clients/projects/holes; `total_depth_m` unchecked
    against sample intervals; furnace tray geometry global; no batch-cancel or
    un-charge remedy. All known; listed so the total weight is visible.

### D. API surface

17. ⚪ **Listing/read gaps compound:** no `GET /api/batches` (list), no
    standalone result GET, no submissions list, no flux-recipe/QC-material
    listing (the UI literally cannot populate dropdowns), no supersession
    history readback, no pagination cursor past 500 samples. Each was deferred
    for lack of a consumer; together they mean the API cannot serve any screen
    more complex than the three that exist. *(Effort M total.)*
18. ⚪ **Hand-written `types.ts`.** Acknowledged standing debt. FastAPI emits
    `openapi.json`; `openapi-typescript` + a CI drift check makes the wire
    contract mechanical instead of aspirational. *(Effort S.)*
19. ⚪ **No API versioning stance.** A breaking response-shape change already
    happened once (deliberately, pre-consumers). Before a client portal or
    Sentinel export exists, decide: URL-versioned `/api/v1`, or
    additive-only-evolution discipline written down. *(Effort S: a doc; the
    discipline is the hard part.)*

### E. Frontend

20. 🟡 **The furnace — the product's most distinctive object — has no
    screen.** Batches, trays, crucibles, QC slots: all invisible. The sample
    screens prove the stack works; the tray is where the system becomes a
    *lab* system instead of a forms system. *(Idea #4.)*
21. ⚪ **Zero frontend tests.** No vitest, no Playwright, nothing renders in
    CI beyond `tsc`. The status-pill colour logic and the 404-vs-error branch
    are exactly the code that regresses silently. *(Effort S-M.)*
22. ⚪ No error/loading discipline beyond per-page ad-hoc handling; no toast /
    retry vocabulary; no date/number formatting locale module.

### F. Testing

23. 🟡 **The OpenAPI contract is never fuzzed.** Hypothesis covers domain
    properties; nothing generates adversarial requests against the live app.
    Schemathesis exists precisely for this and plugs into pytest + CI in an
    afternoon. *(Idea #7. Effort S.)*
24. ⚪ The concurrency behaviours (numbering retry, position races) are tested
    only at unit grain; no test spins parallel workers against one real batch.
    *(Stretch — see idea #11.)*

### G. Operations & observability

25. 🔴 **There are no operational eyes.** `structlog` is declared in
    `pyproject.toml` and imported nowhere; `log_level`/`log_json` settings
    configure a logger that does not exist. No metrics, no traces, no request
    IDs, no slow-query logging. When production says "it was slow at 14:00,"
    the answer will be a shrug. *(Effort S-M: wire structlog with request-id
    contextvars; Prometheus counters; OpenTelemetry later.)*
26. 🔴 **No backup/PITR story.** The compose volume is a single disk. A system
    whose whole pitch is trustworthy records needs documented `pg_basebackup`
    / WAL archiving and a restore rehearsal — an assessor asks for the
    *evidence of the last restore test*, not the policy. *(Effort M.)*
27. ⚪ No healthcheck-driven restart policy on the app container (no app
    container), no graceful-shutdown verification for in-flight batches.

### Summary scorecard

| Area | State |
|---|---|
| Domain modelling | Excellent — the crown jewel |
| Write-path discipline | Excellent — validate-all-then-write everywhere |
| Trust architecture | Strong foundations, **no cryptographic teeth** (findings 1, 2) |
| Security surface | Thin but honest; needs hardening before exposure (7, 8) |
| Lab completeness | Phase-appropriate; instruments/prep/silver/duplicates are the big rocks (11–14) |
| API | Clean conventions, incomplete surface (17) |
| UI | Proof-of-life, not yet a product (20) |
| Testing | Exceptional backend; blind spots at HTTP fuzz + frontend (23, 21) |
| Ops | Pre-operational (25, 26) |

---

## Part III — Research notes

What the outside world does, condensed from primary sources:

**ISO/IEC 17025:2025** replaced the 2017 edition in September 2025 and — for
the first time — includes explicit provisions for information technology,
LIMS, and automated data pipelines. An assessor reading this system against
it will look for: equipment/calibration traceability (clause 6.4 — finding
12), technical records of amendments and how recipients learn of them (7.5 /
7.8 — finding 2), data-integrity controls on automated systems (7.11 — the
audit trail, finding 1), and documented handling of decisions retained over
time. The 2025 edition turns several items in this audit from "nice" to
"scope-defining."

**QA/QC practice in exploration geochemistry** is remarkably standardized:
CRMs judged against certified value ± 2SD per batch; blanks against a
contamination threshold; duplicate pairs analysed by **Thompson–Howarth
plots** (precision estimation even from few pairs, log-log mean-vs-absolute-
difference with 90th/99th percentile control lines) and RPD/CV statistics;
batch-level pass/fail gating whether assay data may enter resource estimation;
all of it quantitative and benchmarkable. Crucially: the *judging* lives in
the geologist's QAQC tooling or the lab's QC module — exactly the boundary
this project draws around QC Sentinel. But the *inputs* (duplicate pairs,
blank results, CRM recoveries with uncertainties) must exist in the LIMS to
export — findings 14 and idea #5.

**Commercial mining LIMS** (Datamine/CCLAS EL, AssayNet, CloudLIMS, LabWare)
compete on: barcode/QR sample tracking and label printing; instrument
integration via file drops and serial capture; client portals delivering CoAs
with **QR codes printed on the certificate for verification**; trend charts
and QAQC dashboards; ISO 17025 alignment as table stakes. Notably, none of
them make the *certificate itself independently verifiable* — the QR leads
back to the vendor's own server. That is a gap, not a ceiling (idea #8).

**Document trust**: PAdES (ETSI EN 319 142) defines PDF signatures with Long-
Term Validation (LTV) — embed cert chains, revocation data, and trusted
timestamps so a signature stays provable for decades (profiles B → T → LT →
LTA). RFC 3161 timestamp tokens carry legal weight (eIDAS-qualified when from
a qualified TSA); **OpenTimestamps** anchors hashes into the Bitcoin
blockchain free of charge, producing compact `.ots` proofs verifiable forever
by anyone with block headers — no vendor, no expiry. The layered pattern
(TSA precision + chain permanence) is now a documented IETF direction. A
certificate of analysis is precisely the artifact class these standards were
built for: signed once, disputed years later.

**High-assurance action confirmation**: banking-grade step-up authentication
binds the WebAuthn/passkey assertion to the *specific transaction payload*
(PSD2 "dynamic linking"): the challenge hashes amount + payee, so the
biometric literally approves those bytes and nothing else. Transposed here:
a certificate issuance ceremony whose passkey assertion covers the PDF's
sha256. The signature stops being "a logged-in manager did something" and
becomes "this human approved this exact document."

**Testing**: Schemathesis derives property-based fuzzing from the OpenAPI
schema itself (built on Hypothesis — same library already in the dev deps);
its `not_a_server_error` and response-conformance checks find the bugs
hand-written tests never reach, including stateful POST→GET sequences.
Deterministic Simulation Testing (FoundationDB → TigerBeetle) shows the
extreme of the same philosophy this repo already holds — determinism,
invariants, reproducible failure seeds — applied to whole systems.

**Geoscience interchange**: OGC **GeoSciML** (boreholes, specimens,
laboratory analyses) and **EarthResourceML** are the formal standards;
practice is humbler — collar/survey/assay CSV tables ingested by Leapfrog,
Datamine, Micromine, acQuire, and dispatch-format imports from ALS/SGS/BV.
Resource teams want: assay import validated *at import*, QAQC gating before
data reaches estimation, and **data freezes** — snapshots locked per resource
update. A LIMS that emits clean collar/survey/assay tables with QAQC status
columns feeds that pipeline natively.

---

## Part IV — The breakthrough agenda

Ranked ideas. "Breakthrough" = moves the system past feature-parity into
territory commercial LIMS leaves empty, or collapses an open question that
has blocked other work. Each states the thesis, the sketch, and why it wins.

---

### #1 · The Ledger That Signs Itself
**Hash-chained audit trail + anchored certificates**

*Thesis.* Finding 1 said it: append-only-by-grant is enforceable against the
app and invisible to everyone else. Make the audit trail self-verifying: every
`audit_event` carries `prev_entry_hash` and `entry_hash = sha256(prev ∥
canonical(entry))`. Any auditor (or this very API) can walk the chain and
detect one flipped bit anywhere in ten million rows. Then give the chain
teeth: periodically anchor the head hash — certificates' `pdf_sha256` at
issuance, daily chain heads — via **OpenTimestamps**, storing the `.ots`
proof beside the row. Verification needs no trust in this database, this
server, or this organisation: just block headers.

*Sketch.* Migration adds two columns (chain computed in the service layer next
to the audit write — one writer, one session, already serialized per request).
A `verify-chain` management command + `GET /api/audit/verify?upto=N`. A small
background task batches `.ots` requests (free, rate-limited friendly).
Certificates gain a QR (see #8) encoding `{cert_number, pdf_sha256}`.

*Why breakthrough.* Commercial LIMS promise "tamper-proof audit trails" and
deliver database permissions. This delivers *mathematical* tamper-evidence a
third party can check offline, decades later. It also retroactively upgrades
every existing guarantee: "append-only by grant" becomes "append-only by
grant, and provably so."

*Scope:* M. Risks: canonical serialization must be pinned (JCS-style, sorted
keys, fixed Decimal rendering — the byte-determinism muscle from the PDF
applies directly).

---

### #2 · The Signing Ceremony
**Passkey step-up bound to the exact bytes a manager signs**

*Thesis.* `MAY_SIGN_CERTIFICATE` is currently a role check — whoever holds
the session signs. Banking solved this: bind the WebAuthn assertion challenge
to the transaction payload (PSD2 dynamic linking). Here: issuance becomes a
two-step ceremony — `POST /api/certificates/intent` returns the rendered-but-
unsigned PDF's sha256 + a WebAuthn challenge containing it; the manager's
device (Touch ID / YubiKey) signs *that hash*; `POST /api/certificates/issue`
verifies the assertion covers the exact bytes, then commits. The stored row
gains `signed_by_credential_id` and the assertion, making "who approved this
document" hardware-provable rather than inferred from a bearer token.

*Sketch.* `py_webauthn` server-side; credentials enrolled per LabUser; the
ceremony doubles as the frontend's first authenticated flow (finding 8), so
one feature closes two findings. Later rung: wrap the signed PDF in PAdES-B-T
with an RFC 3161 token so Adobe Reader shows the green check (LTV/LTA when
archival law demands it).

*Why breakthrough.* Turns certificate signing from an authorisation event
into a *non-repudiation event*. Combined with #1, a certificate becomes: this
human (biometric, device-bound) approved these exact bytes, which existed by
block N. No commercial LIMS ships that sentence true end to end.

*Scope:* M-L (WebAuthn enrolment UX is the long pole). Depends on: nothing.

---

### #3 · Provenance as a Product
**Every sample carries its own evidence dossier**

*Thesis.* The schema already stores the full chain — submission → intake
checks → (prep, soon) → batch/crucible/position → charge weights → parting →
weighing → result chain → certificates — as append-only rows. Nothing reads
it back *as a narrative*. Build `GET /api/samples/{id}/provenance`: a signed,
canonical JSON dossier (hash-sealed like #1) listing every fact, every actor,
every measurement, every refusal-free transition, with the certificate hashes
at the end. Render it as a vertical timeline screen; offer `?format=dossier`
for a downloadable, offline-verifiable bundle.

*Sketch.* Pure assembly query (the anti-join patterns exist); canonical JSON
serializer shared with #1; the timeline is a read-only React page fed by one
endpoint. No new writes, therefore no new risk surface.

*Why breakthrough.* Geologists asking "can I trust this number?" get a
clickable receipt instead of a database dump. Auditors get clause-7.5
technical-records output generated, not compiled. And it showcases the
architecture: the dossier is only possible because the system never
overwrites — it is the append-only design turned into a *demo*.

*Scope:* M. Depends on: #1 for sealing (optional otherwise).

---

### #4 · The Tray
**A digital twin of the furnace, and the batch workflow UI**

*Thesis.* Finding 20: the system's most distinctive object is invisible. Build
the batch screens: a batch list (status pills reuse `StatusPill`); batch
detail as a drawn furnace grid — rows × columns from settings, each slot
coloured by crucible status, QC slots badged with their material type, charged
samples linked to their detail pages; charging/parting/weighing as modal forms
against the existing endpoints. The tray renders from `GET /api/batches/{id}`
which already orders crucibles "the way a technician reads a tray."

*Sketch.* Two pages, one SVG/grid component, generated types (#18) while the
API surface is being touched anyway. Poll or refresh-on-navigate; websockets
later if ever needed.

*Why breakthrough.* Less "research breakthrough," more "the demo stops being
an API tour." A reviewer who sees the tray with CRM slots glowing beside
client samples understands the whole domain model in five seconds. Highest
visibility-per-effort item on this list.

*Scope:* M. Depends on: listing endpoint (finding 17).

---

### #5 · Sentinel's Contract
**The QC dossier: sealed export bundles + local advisory analytics**

*Thesis.* Phase 5 looms as "wire up Sentinel someday." Invert it: define the
export contract *now* as a first-class artifact. On batch completion, build a
**QC dossier**: canonical JSON of every QC row (material, type, inserted
position, portion, bead, derived grade, certified value ± uncertainty),
sealed with its own sha256 (and #1's anchor). Store it content-addressed —
finally the second use case the inline-PDF simplification was waiting for,
promoting both into a real `storage/blob.py`. Add read-side advisory
analytics computed *for display only*: CRM recovery z-scores, blank
contamination flags, and — once duplicates land (finding 14) —
**Thompson–Howarth precision estimates** with the classic control lines.
Never store a verdict; that stays Sentinel's, preserving the separation the
docstrings promise.

*Why breakthrough.* Three debts collapse at once: the blob-store question,
the Phase 5 seam, and "the LIMS shows me nothing about my QC." The analytics
are textbook-standard (Part III), pure functions over data that already
exists, and advisory-only by construction — the architecture's favourite
kind of feature.

*Scope:* M-L. Depends on: duplicates for T-H (else ship CRM/blank analytics).

---

### #6 · The Balance Speaks
**Instrument registry + measurement capture, closing the sensitivity question**

*Thesis.* Findings 12: `instrument` has sat unregistered since Phase 0 while
two open questions wait on it. Register balances (type enum already has
`MICROBALANCE`), record calibrations (dates, sensitivity, certificate refs),
then let result entry resolve `balance_sensitivity_mg` from the balance that
weighed the bead — the oldest open question in the repo, answered by data
instead of convention. Capture raw readings too: a tiny bench endpoint (or
file-drop watcher) accepting the balance's raw serial/web output line, stored
verbatim alongside the parsed value — the "two-meter rule": what the display
said, not just what the operator transcribed. Results gain `instrument_id`,
completing the contamination-traceability chain another open question wants.

*Scope:* M. Unblocks: furnace-per-instrument geometry, AAS/ICP methods (Phase 4).

---

### #7 · Fuzz the Gates
**Schemathesis the OpenAPI contract in CI**

*Thesis.* Finding 23. The repo already believes in property-based testing;
extend the belief to the HTTP edge. Run Schemathesis against the live app in
CI: every endpoint bombarded with schema-valid *and* invalid inputs, asserting
no 500s and full response conformance; enable stateful testing over
OpenAPI links (POST → GET chains) once links are declared. Expected first-week
yield: at least one 500 hiding behind an unhandled IntegrityError or a
Decimal edge.

*Scope:* S. Literally a CI job + link annotations. Highest certainty-of-payoff
item here.

---

### #8 · The Verifier
**QR-sealed certificates anyone can check — even offline**

*Thesis.* CloudLIMS prints a QR that points at their server. Go further:
each CoA's QR encodes `{cert_number, pdf_sha256, ots_anchor_id}`. Scanning
opens a public `/verify` page: paste-or-scan shows the certificate metadata,
recomputes the hash against the uploaded PDF, walks the audit chain (#1), and
checks the OpenTimestamps proof against public Bitcoin headers — with an
explicit "offline verification kit" download (single HTML + `.ots`) for the
paranoid. A client does not need an account, and the lab's server being down
or distrusted changes nothing about the proof.

*Why breakthrough.* This converts the system's core claim — "we issue
documents you can trust" — into something a stranger can falsify in ten
seconds. As a portfolio piece it is the demo that ends the presentation.

*Scope:* M. Depends on: #1 (anchors); #2 optional.

---

### #9 · Two Pairs of Eyes
**Maker-checker option + idempotency keys**

*Thesis.* Regulated labs run four-eyes principles on release decisions. Add:
(a) an optional second-approval step on certificate issuance (draft →
approved-by-second-role → issued, all append-only transitions with reasons);
(b) `Idempotency-Key` support on all POSTs (finding 4) so retried writes are
safe by construction. Together they close the "operator fat-finger meets no
undo" class of incident that append-only systems must prevent *before* the
write, since they cannot correct after.

*Scope:* M.

---

### #10 · The Client Seam
**Scoped reads + dispatch-format exports**

*Thesis.* The CLIENT role is refused everywhere pending a LabUser↔Client
link (documented interim posture). Build it: associate portal users to client
rows; replace `internal_actor` with row-scoped dependencies on exactly the
existing endpoints; add `GET /api/clients/{id}/assays.csv` emitting
collar/survey/assay tables in the Leapfrog/acQuire-friendly shapes resource
teams import (Part III), with QAQC status columns and stable column contracts.
Later: GeoSciML/ERML serialization as an alternative encoder behind the same
dossier model (#3).

*Why breakthrough.* Flips the system's biggest refusal into its growth
story: the lab's clients start pulling data instead of phoning for PDFs.

*Scope:* L (schema + scoping + format contracts). Sequences with Phase 6.

---

### #11 · Rehearsal *(stretch)*
**A deterministic harness for the concurrency paths**

*Thesis.* DST (Part III) at toy scale: inject a seeded clock, seeded id
source, and fault schedule behind interfaces the services already almost have
(settings, allocator); simulate N writers racing the numbering allocator and
position claims, assert invariants (no duplicate numbers issued, no double
booked slot, chain intact). Honest scope: FoundationDB-style whole-system
simulation this is not — but the numbering/booking paths are exactly the
subset where interleavings matter and where a reproducible failing seed is
worth its weight.

*Scope:* L-ish, speculative. Do after #7 proves the appetite.

---

### #12 · Silver by Difference *(chemistry completeness)*
*Thesis.* Finding 13. Columns for Ag (`prill`-derived, silver_by_difference
with its own censoring rules), the eager-vs-on-request decision recorded in
the decision log, certificates printing Au-Ag pairs. Chemically it is sitting
right there in the parting measurements. Scope M; natural companion to Phase
4's multi-element push.

---

## Recommended sequence

If only three things happen next:

1. **#7 Fuzz the Gates** — one afternoon, permanent regression shield, likely
   finds real bugs immediately.
2. **#4 The Tray** — the visible product leap; forces the listing endpoints
   (finding 17) and generated types (#18) through the same door.
3. **#1 The Ledger That Signs Itself** — the identity move: everything else
   in the trust stack (#2, #3, #5, #8) bolts onto its hashes.

Then let #2 (signing) ride the first frontend auth work, #5 ahead of Phase 5,
and #10 as the Phase 6 centrepiece.

---

## Sources consulted (2026-08-25)

- ISO/IEC 17025:2025 edition coverage (Zendo LIMS primer; Scispot clause map;
  Datamine accreditation notes)
- QA/QC: Demetriades & Argyraki 2025 Thompson–Howarth tutorial (IUGS/GGB,
  Zenodo 15557648); Stanley/Abzalov duplicate-methods survey (GEEA 2024);
  SGS QA/QC plan acceptance criteria; Bureau Veritas RPD guidance
- Mining LIMS landscape: Datamine Laboratories (CCLAS EL/AssayNet),
  CloudLIMS mining solution notes (QR-on-CoA, instrument integration),
  LIMS.science metals/mining configuration
- Timestamping & signatures: OpenTimestamps; RFC 3161 + blockchain-anchoring
  analyses (MDPI Appl. Sci. 2025; IETF SCITT time-anchor draft); ETSI PAdES
  LTV explainers (Encryption Consulting; DocuSign LTV reference)
- Step-up auth: Yubico WebAuthn implementation guidance; PSD2 dynamic-linking
  binding (MojoAuth 2026)
- Testing: Antithesis DST docs; TigerBeetle VOPR; Amplify DST primer;
  Schemathesis docs & ACM artifact (v4)
- Interchange: OGC GeoSciML 4.1; CGI/IUGS EarthResourceML; Seequent drilling
  import formats; RaftLabs geological-data-management integration notes
