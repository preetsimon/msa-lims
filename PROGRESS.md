# MSA LIMS — Progress

**Updated:** 2026-08-24 · **Phase:** 1 in progress (auth, client/project/submission intake done; results next)

New to the codebase? Read [docs/ENGINEERING_GUIDE.md](docs/ENGINEERING_GUIDE.md)
first — it explains the decisions this document only tracks.

---

## Status at a glance

| Phase | Window | State |
|---|---|---|
| 0 · Skeleton | wk 1 | **Done** — domain core, schema, grants, CI gate, UI shell |
| 1 · The spine | wk 2–4 | **In progress** — auth, client/project registration, submission intake done; result entry → certificate next |
| 2 · Fire assay batching | wk 5–7 | Not started |
| 3 · Lifecycle & prep | wk 8–9 | Not started |
| 4 · ICP & bulk import | wk 10–11 | Not started |
| 5 · The Sentinel seam | wk 12–13 | Not started |
| 6 · Ship the story | wk 14–16 | Not started |

**Health:** 213 tests passing · ruff clean · mypy `--strict` clean · migrations
apply from empty · frontend builds and typechecks · verified live through the
browser: Vite → proxy → FastAPI → Postgres, with the degraded path exercised;
auth, client/project registration, and submission intake all verified live
against the running server — a client and project registered through the API
now feed a submission with no direct DB insert required.

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
  `find_overlaps` reports every conflicting pair, not the first.
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

### Database — `src/msa_lims/db/`
- 8 tables: `client`, `project`, `drill_hole`, `submission`, `sample`,
  `instrument`, `lab_user`, `audit_event`.
- `7172b2adeb7e` — initial schema.
- `a64c168cff52` — audit events.
- `b1d0c4e77a10` — **append-only grants**. Creates `msa_app` with no
  UPDATE/DELETE on `audit_event`, and deliberately **no `ALTER DEFAULT
  PRIVILEGES`**: a table added in a later migration gets no grants until someone
  decides, in a reviewable diff, whether it is mutable or append-only.
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

### Frontend — `frontend/`
- React 18 + TypeScript + Vite, `strict` plus `noUncheckedIndexedAccess` and
  `exactOptionalPropertyTypes`.
- Dev server proxies `/api` and `/health` to the backend, so there is one origin
  and no CORS configuration to get wrong in dev and differently wrong later.
- `types.ts` mirrors the wire format including the censored-value distinction,
  with `formatMeasured` so a non-detect cannot be rendered as its null value.
- One screen so far: system status, which exists to prove the whole path.

### Tests — 213
- **Unit** (139): units and dimensions, censored values, assay arithmetic,
  sample labels and intervals, the state machine, OIDC token verification
  against a real self-signed keypair, the auth dependency exercised through a
  real FastAPI app (`TestClient`) across dev-header and OIDC modes.
- **Property** (17, Hypothesis): conversion round-trips within working
  precision; mass conversions exact; substitution always lands within the limit;
  the inverse grade calculation recovers its input; contiguous intervals never
  conflict; generated labels parse back to their parts.
- **Integration** (57, real Postgres): the append-only grants proven against the
  actual application role; submission intake against the service directly and
  through the real HTTP app (26 tests — happy paths, every validation refusal,
  the audit trail, role enforcement, `LabUser` provisioning); client and
  project registration against the service directly and through HTTP (21
  tests — duplicate code/name/project-name refusals, cross-client isolation,
  role enforcement, audit trail, and one test that registers a client and
  project purely through the API and then submits a sample against them, with
  no direct DB insert anywhere in the chain).

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
succeeds with **201** — the first time the whole spine has been driven purely
through HTTP with no direct database insert anywhere in the chain.

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
4. A `POST /api/drill-holes` (or similar) registration endpoint. Submission
   intake still *requires* a hole to pre-exist and there is still no way to
   create one through the API — only through a direct DB insert, which is
   what the tests do. This is now the **only** remaining gap blocking a real
   demo of drill-sample intake purely through HTTP.
5. Fire assay result entry against `domain/assay.py`, stored append-only with
   supersession, writing an `audit_event` per change.
6. Certificate of analysis: versioned row, byte-deterministic PDF, amended
   never overwritten.
7. Sample list and detail screens in React over those endpoints.

## Open questions

- **Which balance sensitivity is real?** `gravimetric_grade` takes it as a
  parameter, but the value should come from the instrument record once
  `instrument` carries calibration data. Currently every caller must supply it.
- **Does the lab report silver on every fire assay, or only on request?** Drives
  whether `silver_by_difference` is computed eagerly at bead entry or on demand.
- **Submission numbering.** `SUB-2026-0841` is invented. Needs the real
  convention before Phase 1 hardens it into stored data.
- **Does a sample ever move between submissions?** Currently `submission_id` is
  NOT NULL with no history. If re-submission happens, that is a chain, not an
  update.
- ~~**`Actor` vs. `LabUser`.**~~ **Resolved 2026-08-24** — see
  `current_lab_user` in `web/deps.py` and the decision log above.
- ~~**Client onboarding and project registration.**~~ **Resolved 2026-08-24** —
  see the Client and project registration section above.
- **No endpoint registers a `DrillHole` yet.** Submission intake requires one
  to already exist; the tests seed it by inserting the row directly. This is
  now the last piece of reference-data registration missing before a real
  demo of the spine can run entirely through HTTP.
- **No endpoint deactivates a client** (`Client.is_active`) or amends one
  already registered. Out of scope for now — nothing downstream reads
  `is_active` yet, so there is nothing to demonstrate it changing.
