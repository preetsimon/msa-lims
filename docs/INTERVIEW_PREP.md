# MSA LIMS — Interview Preparation

**Companion to:** [PROGRESS.md](../PROGRESS.md) (what was built, and every
decision's reason) · [docs/ENGINEERING_GUIDE.md](ENGINEERING_GUIDE.md) (the
architecture's *why*) · [docs/AUDIT_AND_BREAKTHROUGHS.md](AUDIT_AND_BREAKTHROUGHS.md)
(known gaps and the forward agenda).

How to use this: every answer is written the way you should *say* it — first
person, concrete, grounded in a file or a test you can name. The follow-ups are
the questions a strong interviewer asks next. If an answer needs a number,
it's in the cheat-sheet at the end.

---

## 1 · The pitches

**Q: Tell me about this project in 30 seconds.**

> It's a LIMS — laboratory information management system — for fire assay
> gold analysis: the system of record for samples arriving from exploration
> drilling, moving through preparation and furnace batches, producing
> append-only assay results, and ending in signed Certificates of Analysis.
> It's Python 3.11 and FastAPI over Postgres, with a pure domain layer that
> does exact rational arithmetic — no floats anywhere near a reported grade —
> plus a React/TypeScript frontend. The thing I'm proudest of is the trust
> architecture: results, certificates and audit events are append-only *by
> database grant*, not by convention, so even the deployed application
> physically cannot rewrite history.

**Q: And in two minutes?**

> The domain layer is pure Python — no sessions, no clock — with value types
> like a censored MeasuredValue that refuses float conversion, and a
> gravimetric grade calculation where a bead below balance sensitivity yields
> a non-detect at the corresponding grade rather than a tiny number. Services
> own workflows: submission intake validates an entire batch before writing
> anything, result entry builds supersession chains instead of updates, and
> certificate issuance freezes the exact result rows it reports. On top of
> that, immutability isn't a code review promise — there's a restricted
> Postgres role whose grants make UPDATE on results literally fail, and a
> test connects as that real role to prove it. Auth is OIDC with roles mapped
> explicitly from group claims; there's deliberately no config flag that
> turns signature verification off. Batches model the physical furnace: tray
> positions, per-crucible flux scaled by Decimal arithmetic, QC materials
> inserted beside client samples, parting and weighing recorded at the bench.
> Testing is 470 tests: property-based tests over unit conversions and label
> parsing, integration tests against real Postgres as the restricted role,
> byte-determinism proofs for the PDF generator, and CI that migrates from
> empty and downgrades to base on every push.

---

## 2 · Architecture on a whiteboard

**Q: Walk me through a request end to end.**

> Take `POST /api/fire-assay-results`. FastAPI routes it through dependencies
> in `web/deps.py`: `current_actor` resolves who's calling — verifying a
> bearer token against the IdP's published keys in oidc mode, or the dev-
> header shim in local/ci — then `current_lab_user` provisions or fetches a
> durable LabUser row keyed on the token subject, purely so audit rows have
> something stable to reference. The route parses the body through a Pydantic
> schema, hands a frozen dataclass input to `FireAssayResultService`, which
> does all validation *before* any write: role gate against MAY_ENTER_RESULTS,
> sample existence, crucible provenance if one is named — deriving the portion
> weight from the recorded charge rather than trusting a re-typed number —
> supersession legality. Then the grade computation happens in the pure
> domain layer. Only then does anything flush: the append-only result row,
> one audit event, the sample status move — one transaction, committed by the
> route. Errors are raised as typed exceptions and mapped globally to distinct
> status codes — 403 wrong person, 409 state moved, 422 bad request.

*Follow-up: why do routes commit rather than the dependency?*
> Because the service must control transaction scope atomically — the audit
> event and its subject row commit together or not at all. A middleware commit
> on success would also commit partial work after an unexpected late error;
> explicit commits at the route keep the write boundary visible in review.

**Q: Draw the layers.**

> Four: **domain/** at the bottom — enums, units, values, assay math, state
> machines; imports nothing but stdlib. **db/** — SQLAlchemy models and
> session machinery. Feature **services** (`batches/`, `certificates/`,
> `fire_assay_results/` …) — orchestration, validation, authorization, audit;
> they import domain downward and each other *acyclically*, which is why some
> errors live in "surprising" modules — `SampleNotFoundError` stays in
> fire_assay_results because samples/service.py already imports from there,
 and hoisting it back would close a cycle for no benefit. **web/** — routes,
> schemas separate from ORM models so column renames aren't API breaks, and
> global error→status mapping. Dependencies only point one direction.

---

## 3 · Domain modelling

**Q: Why is MeasuredValue such a central type? What does it buy you?**

> Assay results are *censored*: below detection, the honest answer is "<
> 0.01 g/t", not zero. A float can't carry that distinction, and reporting a
> fake zero is how contaminated ground gets mined. So `MeasuredValue` keeps
> value, detection limit, a censored flag and unit together, and it has **no
> `__float__`** — you cannot silently degrade it. To get a number out you must
> call `require_detected()` or name a substitution rule explicitly, so the
> choice is visible at the call site. There's a property-based test asserting
> substitutions always land within the limit.

**Q: Exact rationals — why bother? Isn't float fine for lab numbers?**

> Two reasons. Reproducibility: a certificate is a legal document; recomputing
> a grade five years later must give bit-identical answers, so conversions use
> exact integer-scaled factors under a pinned decimal context — precision never
> depends on ambient state. And the assay ton: it's exactly 175/6 grams, and
> the whole point of that constant is the identity that 1 mg per assay ton is
> exactly 1 oz/ton. With floats that identity is approximately true; we have a
> unit test asserting it exactly. Cross-dimension conversion is refused —
> turning milligrams into g/t requires the sample weight, so it's a named
> calculation, not a unit conversion. Modelling that refusal in the type
> system stopped a whole class of silent nonsense.

**Q: How does the grade calculation handle a non-detect?**

> In gravimetric fire assay, if the bead weight is at or below what the
> balance can resolve, the honest report is "not detected" at the grade that
> sensitivity corresponds to. `gravimetric_grade` returns a censored
> MeasuredValue in that case. One subtlety the audit caught: a bare *zero*
> bead with no stated sensitivity is ambiguous — instrument noise or a real
> zero? We refuse it and name the remedy: state the balance sensitivity,
> which turns the same reading into a proper non-detect. Refusing beats
> guessing a convention nobody documented.

*Follow-up: tell me about the rounding bug.*
> Live verification caught it: a 0.160 mg bead over a 30 g portion doesn't
> divide terminally, and the full 34-digit Decimal artifact printed straight
> onto the signed PDF — "5.333333… g/t". The fix respects the architecture:
> the stored `au_value` keeps full precision for audit and recalculation;
> rounding to three decimals, ROUND_HALF_EVEN, happens *only* at display, in
> the certificate renderer. Display-only fixes must never mutate stored truth.
> There's a unit test pinning the non-terminating case, the clean case, and
> that non-detect limits are never rounded.

**Q: Why are depth intervals half-open?**

> Contiguous sampling is the normal case — 142.0–144.0 followed immediately
> by 144.0–146.0. Under closed intervals that pair looks like an overlap and
> every legitimate core run trips the check, tempting a tolerance fudge.
> Half-open [from, to) makes adjacency unambiguous. And when overlaps *do*
> exist, `find_overlaps` reports every conflicting pair in one pass, not the
> first — mirrors how intake validation reports all problems together, because
> a submitter fixing one label doesn't want to discover the next error on
> retry.

---

## 4 · Integrity, Postgres, and the trust architecture

**Q: You say "append-only by grant." Why is that better than enforcing it in
the service layer?**

> Because service-layer rules have exactly the enforcement power of code
> review. Any bug, script, migration, or future developer with a session can
> UPDATE a row and nothing resists. We create `msa_app` as a restricted role
> holding SELECT/INSERT only on `audit_event`, `fire_assay_result`,
> `certificate`, `certificate_result` — the application connects as that role
> in every environment including tests. Postgres itself refuses the UPDATE.
> There's a test class that connects as the real role and asserts both UPDATE
> and DELETE raise `InsufficientPrivilege`. Corrections go through
> supersession — a new row pointing at the old — which preserves history
> instead of replacing it.

*Follow-up: who CAN edit those tables then?*
> The schema owner, `msa` — used by migrations. That's deliberate: an owner
> bypasses RLS-style restrictions, and migrations need DDL anyway. The point
> isn't that no human can ever touch it; it's that the application *cannot*,
> so tampering requires a deliberate act outside the application's authority,
> which audits can look for.

**Q: Schema and grants are separate migrations. Why split them?**

> Because they're two different decisions. Table shape changes on technical
> grounds; mutability is a governance decision — "may the app rewrite this?"
> Every table lands in two reviewable diffs, and there's deliberately no
> `ALTER DEFAULT PRIVILEGES` anywhere, so a new table is born with *no*
> grants: inaccessible until someone explicitly decides its tier in review.
> The failure mode without this is loud on first use — good — versus the
> alternative, a results table that quietly turned out editable forever.

**Q: Why VARCHAR + CHECK for enums instead of Postgres native enums?**

> Native enums store values in a system catalog; removing or renaming a value
> is effectively a table rewrite with locks, and adding values needs DDL. Our
> vocabularies are small and stable but not *frozen* — QC material types grew
> — and a CHECK constraint gives identical integrity for a plain ALTER. The
> vocabulary lives in Python enums so a typo fails at import time, and the
> CHECK mirrors it at the database so a raw psql insert can't invent states.

**Q: How do you allocate document numbers safely — SUB-2026-0841?**

> Honestly: COUNT+1 under a documented single-writer assumption, made
> race-safe with a savepoint. Two concurrent requests can compute the same
> number; one would die on the UNIQUE index with an unhandled 500. Allocation
> now retries inside `begin_nested()` — only on SQLSTATE 23505, so real
> constraint bugs still surface loudly — recomputing from the live count.
> No schema change, no sequence to grant, and crucially the certificate path
> needed no post-insert UPDATE, which would have violated append-only. The
> numbering *shape* is still provisional — flagged in PROGRESS.md until the
> lab's real convention is known. I'd rather ship an honest placeholder than
> invent policy.

**Q: Why is "current result" computed by exclusion instead of an is_current flag?**

> A boolean flag must be flipped in two places on every correction and drifts
> the first time someone forgets. Exclusion has nothing to sync: current is
> defined as "no other row's supersedes_id points here," derived from the
> same facts every time. Implementation is a LEFT JOIN / IS NULL anti-join
> against an aliased successor, scoped to the sample, so the planner uses
> indexes — the earlier NOT IN version scanned the whole table's supersession
> set; the audit rewrote it and added a three-link chain test. Supersession
> chains also refuse branching: only the chain's head can be superseded,
> otherwise two "corrections" could both claim currency.

**Q: A certificate references results how?**

> It freezes them. `certificate_result` stores the specific fire_assay_result
> row id per sample at issuance — not a live query. A certificate is a
> historical statement: if the result is later superseded, the certificate
> still records exactly what it said that day. That's also why issuing moves
> the sample RECEIVED-chain status through the *real* ASSAYED→REPORTED
> transition via `check_transition` — the one place the modelled path is
> honestly reachable — while re-certifying an already-reported sample leaves
> status alone.

---

## 5 · Authentication & authorisation

**Q: Describe the auth design.**

> OIDC bearer verification ported from another system of mine: fetch the
> provider's JWKS, verify signature/issuer/audience, map groups to roles via
> an explicit env mapping — unmapped groups grant *nothing*, no analyst
> fallback. Two modes chosen by MSA_AUTH_MODE: real oidc, and dev_headers for
> local/CI which is refused with 501 anywhere else — even if someone
> compromises the shim, it can't silently work in deployment. There is no
> configuration that disables signature verification, and a test reads the
> module's source and asserts the kill-switch string never appears. 401 and
> 403 are kept distinct because they send people to different places — login
> again versus ask an administrator.

**Q: Actor vs LabUser — why both?**

> The actor comes fresh from the token every request; the LabUser row exists
> only so foreign keys — audit_event.actor_id — have something durable to
> point at across requests. Provisioned on first sight keyed on the provider
> subject, never name or email since people change those. Its stored role is
> a courtesy mirror for joins; *no authorisation check ever reads it* — every
> check reads Actor.role from the current request, so a stale DB row can't
> outlive what the IdP currently grants.

**Q: Where does CLIENT sit in your privilege model and why?**

> Deliberately at the *bottom*, below every internal role. Privilege ordering
> is resolved by "most privileged group wins" for people holding multiple
> groups; without ordering, someone in both `clients` and `lab-analysts`
> could resolve to CLIENT and lose their staff rights, or worse cases in
> other orderings. Ordering makes resolution deterministic, and a test pins
> the mixed-membership case.

**Q: Clients can't read anything right now — isn't that a product hole?**

> It's an honest interim posture. Reads require internal roles because there
> is no LabUser↔Client association to scope rows by; open reads would let
> any client account read any other client's grades by id enumeration. The
> alternative — shipping access justified by "nobody malicious uses the
> demo" — is exactly how data leaks happen. So `internal_actor` refuses with
> a message naming the missing infrastructure, and the client portal is a
> designed-for piece of the roadmap: add the link, swap the dependency on
> exactly those endpoints.

---

## 6 · Workflows & state machines

**Q: Sample charging bypasses your own lifecycle table. Defend that.**

> Gladly — it's documented in the module docstring, not hidden. Before
> writing batching I checked every call site of `check_transition`: one,
> certificates. Nothing in the system could bring a sample to READY_FOR_ASSAY
> because prep-stage tracking doesn't exist yet — Phase 3. Routing charging
> through the transition table honestly would only succeed for samples that
> arrived there by a path that doesn't exist. So charging accepts any
> pre-assay sample and moves it straight to IN_ASSAY, stated as what the
> system currently tracks. When prep lands, the bypass shrinks. The
> alternative — adding a fake RECEIVED→IN_ASSAY row to the shared table —
> would misrepresent the lab's real process permanently to win temporary
> convenience.

**Q: The batch state machine vs the sample state machine — why two?**

> They answer different physics. A sample can come *back* — re-assay returns
> it to READY_FOR_ASSAY. A batch describes a furnace run that already
> happened; there is no honest "un-fire." So the batch machine is strictly
> linear, no branches, reusing the same exception types as the sample
> machine — a skipped furnace stage and an illegal sample move are the same
> refusal family, and callers catch one vocabulary. Inside a batch,
> fusion/cupellation advance every crucible in lockstep because the furnace
> acts on the whole tray, while parting and weighing are hand-driven
> per-crucible moves in a deliberately *partial* transition set — no direct
> CHARGED→PARTED edge that no endpoint offers. Status advances always carry
> witnessing measurements: parting without button/prill/acid is refused — a
> status flip with nothing behind it claims something about the world nobody
> observed. Database CHECKs mirror it.

**Q: How do you charge a crucible — and a QC material?**

> One endpoint carrying exactly-one-of `sample_id` or `qc_material_id` —
> both and neither refused upfront, and the exclusivity is also a CHECK
> constraint. Same bench-role gate, same batch-must-be-CHARGING gate, same
> position bounds and uniqueness checks, same flux scaling: a recipe states
> proportions calibrated at a nominal portion, doubling the weighed charge
> doubles every reagent — linear scaling is the entire physical premise, done
> in pinned-context Decimal and computed *once at charge time*, stored on the
> row, so editing a recipe later never rewrites what a technician actually
> weighed. QC differs downstream: no sample lifecycle moves, and fire assay
> results can never name a QC crucible — its bead is judged by our companion
> QC system on export; the LIMS records insertion and measurement only.
> Insertion is recorded, not enforced: how many controls a batch needs is QA
> policy, half the QC vocabulary (sample duplicates) isn't modelled yet, and
> a counting rule written today would guard half the picture.

**Q: Flux recipe lives on the crucible, not the batch. Why?**

> Caught during design, before schema. One furnace load routinely fires a
> silicate core beside a sulfide one; each needs its own flux chemistry. A
> batch is a shared furnace slot, not a shared formula. Modelling it on Batch
> would have been a migration to walk back within weeks.

---

## 7 · HTTP & API design

**Q: Explain your status-code philosophy.**

> Distinct refusals mean distinct things, so they get distinct codes: 403 —
> find someone with authority (role gates); 409 — the world moved under you
> (illegal transitions, occupied slot, double parting); 404 — no such
> resource, including a crucible that exists but belongs to another batch,
> because from that URL it genuinely doesn't; 422 — the request itself is
> wrong, carrying *every* problem in one list, not just the first. Collapsing
> these to 400 throws away information clients act on differently. Error
> bodies pass through the service messages, which are written for the person
> hitting them and name remedies.

**Q: Request shapes — any strong opinions?**

> Yes: either/or fields beat optional-everything. A result names *either* a
> crucible *or* raw weighings — never both, not neither; naming a crucible
> derives weights from recorded provenance, refusing retyped copies that
> could contradict reality. Charging names exactly one of sample/QC material.
> Where a mode genuinely doesn't exist yet — AAS/ICP methods read calibration
> curves, not bead weights — there's no dead `method` parameter pretending to
> validate it; the method gets its own endpoint with its own shape later.
> Schemas stay separate from ORM models so wire contracts survive internal
> renames.

**Q: GET /api/batches/{id} orders crucibles "how a technician reads a tray."
Why does that matter?**

> Small thing, real principle: row-major top-left ordering matches the
> physical tray, so screen and bench agree without translation. Domain
> details like that are cheap now and expensive to retrofit into habits.

---

## 8 · Testing

**Q: Your integration suite runs as the restricted role. Why is that special?**

> Most projects test as superuser, which means grants are decoration. Ours
> binds every fixture session to `msa_app`, so services execute under exactly
> production privileges — a service that needed a forbidden grant fails in
> CI, not deployment. Same fixtures prove enforcement positively: direct
> UPDATE/DELETE attempts against results, certificates, audit — refused by
> Postgres, asserted as `InsufficientPrivilege`.

**Q: Property-based testing — where did it actually pay off?**

> Unit conversions round-trip within working precision and mass conversions
> stay exact under Hypothesis-generated values; generated sample labels parse
> back to their parts; contiguous intervals never conflict; inverse grade
> calculation recovers its input; substitutions land within detection limits.
> Each encodes a law rather than an example. The audit found parse gaps
> (`<0`, negative readings accepted) that became refusals with their own
> property tests. Next step, in the audit doc: Schemathesis fuzzing the
> OpenAPI contract — same philosophy at the HTTP edge.

**Q: Byte-deterministic PDFs — why and how?**

> Why: a certificate must be regenerable years later, byte-identical, or at
> least provably produced from the same inputs — and determinism is what lets
> a test assert it rather than hope. How: reportlab's Canvas takes
> `invariant=1` to suppress the random document ID stamped into trailers, and
> we embed only the standard 14 fonts, so nothing platform-dependent gets
> embedded. Tests render twice and compare bytes — identical content equal,
> different content unequal — including an 80-sample pagination case and
> word-wrapped paragraphs using exact font metrics. Text wrapping matters:
> v1 ran long supersession reasons off the page edge of a *signed document*.

**Q: Migration testing?**

> CI migrates from empty on every push — a migration that only works on a
> database someone already had isn't done — then downgrades to base and back.
> That reversibility check earned its keep immediately: the first batching
> migration copied Submission's literal unique-constraint name `"number"`
> and failed outright — Postgres backs UNIQUE constraints with indexes whose
> names are unique per *schema*, not per table. Fix went structural: rely on
> the declared naming convention for table-qualified names, killing the
> collision class instead of patching one instance.

---

## 9 · Frontend

**Q: React here while your other project is server-rendered and JS-free — justify.**

> Different systems, different constraints. Sentinel serves air-gapped plants
> where no-JS is a requirement; this is office-side software. React with
> TypeScript strict — plus `noUncheckedIndexedAccess` and
> `exactOptionalPropertyTypes`, which most teams leave off — exercises
> rigorous frontend typing, which was a goal. The dev server proxies /api so
> there's one origin and zero CORS configuration to get wrong twice — dev
> and prod — differently.

**Q: What's the frontend's biggest gap, honestly?**

> It authenticates as nobody — relies on dev-header defaults — and there are
> no component tests. Both are first-class items in the audit doc; the passkey
> signing ceremony is the natural forcing function to build real auth against,
> because signing a certificate deserves hardware-backed identity anyway.

---

## 10 · Rapid-fire: why X over Y

| They ask | You answer |
|---|---|
| Monolith vs microservices? | Modular monolith, deliberately. One deployable, acyclic module boundaries enforced by import direction. A LIMS's consistency needs are absolute — a sample and its result in one transaction. |
| REST vs GraphQL? | REST with flat resource creation (child ids in body, matching one convention for parentage). No query-shape flexibility needs yet; GraphQL would be ceremony. |
| SQLAlchemy vs raw SQL? | ORM for the object graph and portable expressions; but performance-critical reads written as explicit queries (the anti-join), and grants/migrations as raw SQL because they're Postgres contracts, not app code. |
| Alembic autogenerate vs hand-written? | Autogenerate then *edit*: the colliding constraint name came from accepting generated literals blindly. Reversibility is written, not generated. |
| UUID vs bigint PKs? | Bigints: smaller indexes, honest ordering, and exposure-safe behind role-gated APIs. Natural keys (labels, numbers) are UNIQUE constraints, not PKs. |
| Datetime handling? | Business timestamps `timestamptz` supplied by callers ("when it happened ≠ when entered"); known debt: mixin `created_at` naive — queued fix. |
| Sync or async FastAPI? | Sync endpoints + threadpool simplicity; DB probe wrapped in `asyncio.to_thread` after the audit caught it blocking the loop. Async everywhere buys nothing behind one Postgres. |
| Docker per service? | Compose ships Postgres only today; app runs from venv. Honest for dev; containerizing the app + pip-audit is on the ops list. |
| is_current flag vs exclusion? | Exclusion — nothing to desynchronize. (Detailed above.) |
| Enforce QC insertion or record it? | Record. Policy without its full vocabulary (duplicates) guards half the picture; recording is the prerequisite either way. |

---

## 11 · War stories (tell these proactively)

1. **The 34-digit grade.** Live curl verification printed
   `5.333333333333333333333333333333333 g/t` onto a signed PDF. Fix:
   display-only rounding (HALF_EVEN, 3dp); stored precision untouched; four
   tests pinning the behaviour. Lesson: verification catches what unit tests
   don't think to ask; rendering is not storage.
2. **`relation "number" already exists.`** Copy-pasted constraint name across
   tables; unique index names are schema-global. Structural fix via naming
   conventions. Lesson: reversibility testing pays the first week.
3. **The zero-bead conflation.** Audit found a detected-looking `0 g/t` from
   a zero bead with no sensitivity — precisely the confusion MeasuredValue
   exists to prevent. Now refused unless sensitivity converts it to a
   non-detect. Lesson: audits against a green suite find what passing tests
   encode wrongly.
4. **Health probe blocking the event loop.** A sync DB ping inside async def;
   fixed with `asyncio.to_thread`. Lesson: framework hygiene is findable by
   reading your own code adversarially.
5. **Race-safe numbering.** COUNT+1 + savepoint retry on 23505 only. Lesson:
   document single-writer assumptions loudly, then engineer the crash path
   anyway.

---

## 12 · Known debts you should volunteer before being asked

Saying these unprompted signals seniority:

- Prep stages don't exist → lifecycle shortcuts documented as such (Phase 3).
- Instruments unregistered → balance sensitivity caller-supplied; no
  equipment traceability yet (ISO 17025 clause 6.4 gap).
- No read auditing, rate limiting, security headers, backup/PITR story;
  structlog declared but unwired.
- Sequential numbering shape provisional; inline PDF storage pending a second
  blob use-case (QC dossiers will be it).
- Hand-written TS types pending OpenAPI generation; no frontend tests.
- Full list, graded: docs/AUDIT_AND_BREAKTHROUGHS.md.

Then pivot: *"here's the ranked plan for each"* — the hash-chained audit
ledger, passkey-bound signing, tray UI, Schemathesis in CI.

---

## 13 · Cheat sheet — numbers to know cold

- **Python 3.11**, FastAPI, SQLAlchemy 2, Alembic, psycopg 3, pydantic v2;
  reportlab (optional extra), PyJWT[crypto]; structlog declared.
- **Postgres 16** on port **5435** (own container — clone-and-run isolation).
- **15 tables** · **13 migrations** · **18 write+read endpoints**
  (13 POST/PATCH writes, 5 GETs).
- **470 tests**: 192 unit, 17 property, 261 integration (real Postgres).
- Roles: prep_tech < analyst < supervisor < lab_manager; client external.
  Tiers: BENCH_ROLES, MAY_ENTER_RESULTS, MAY_SIGN_CERTIFICATE (manager only),
  MAY_MANAGE_ACCOUNTS, MAY_CONFIGURE_LAB.
- Audit actions vocabulary: create · amend · supersede · transition.
- Units: 11 across mass/volume/concentration; assay ton = exactly 175/6 g.
- Furnace default 6×6; default portion 30 g; numbering BATCH/SUB/COA-YYYY-NNNN.
- Certificate PDF: invariant=1, standard-14 fonts, sha256 verified on download.

## 14 · Questions to ask *them*

- "What's your data-retention/immutability story — grants, event sourcing,
  WORM storage?" (invites your favourite topic)
- "How do validation failures reach users — first error or all of them?"
- "Who owns migration reversibility when a hotfix ships?"
- "Where does QA policy live — code, config, or the lab's SOP documents?"

---

*Every claim above traces to code or PROGRESS.md. If you can't defend an
answer with a file or a test name, reread that section — that's the one an
interviewer will drill.*
