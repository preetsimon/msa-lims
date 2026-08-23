# MSA LIMS — Engineering Guide

**For developers joining this codebase.** This explains what the system is, the
decisions behind it, and why each one was made. Read it before writing code —
most of what looks unusual here is deliberate, and the reasoning matters more
than the syntax.

If you are looking for *what is built and what is next*, that is
[PROGRESS.md](../PROGRESS.md). For the two-system topology, that is
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. What this system is, in one paragraph

A mining company drills rock and wants to know how much gold is in it. It sends
bags of core to a laboratory. The lab crushes and pulverises the rock, weighs a
portion, melts it with flux so the gold collects in molten lead, burns the lead
away, weighs the bead that remains, and calculates a grade. MSA LIMS tracks
every one of those steps for every bag, and issues the certificate that states
the answer. That certificate is a legal document under NI 43-101; a mining
company raises money on it.

The one sentence to keep in your head:

> **Every number this system reports is traceable to a weighing, and no number
> it reports was invented to make an arithmetic operation work.**

Almost every design decision below is downstream of that sentence.

---

## 2. Vocabulary you need first

The domain is unfamiliar to most developers. You cannot read the code without
these.

| Term | What it means |
|---|---|
| **Assay** | A measurement of how much of an element is in a sample. |
| **Fire assay** | The classical method for gold: melt the sample with flux, collect the precious metal in molten lead, then burn the lead away (*cupellation*), leaving a bead to weigh. |
| **Flux** | The reagent mixture a sample is fused with — litharge, soda ash, borax, silica, flour, nitre. The recipe depends on what the rock is made of. |
| **Crucible** | The fireclay pot one sample is fused in. A furnace tray holds a grid of them. |
| **Cupellation** | Oxidising the lead away in a porous cup, leaving the precious metals behind. |
| **Doré bead** | What cupellation leaves: gold **and silver** fused together. |
| **Parting** | Dissolving the silver out of the doré bead with nitric acid, leaving gold. |
| **Prill** | Another word for the bead. Ambiguous about whether silver is still in it, which is why this codebase says `dore_bead_mg` and `gold_bead_mg` instead. |
| **Pulp** | Rock pulverised to a fine powder, ready to weigh out. |
| **Assay ton** | The traditional fire assay portion, exactly 175/6 g ≈ 29.1667 g. Chosen so 1 mg of bead = 1 troy oz/short ton. |
| **Analyte** | The element being measured — Au (gold), Ag (silver), Cu (copper). |
| **CRM** | Certified Reference Material: a material with a known certified concentration, run like a sample to check the instrument. |
| **Blank** | Material containing none of the analyte. Should read ~zero; if not, something is contaminated. |
| **Duplicate** | The same material measured twice. |
| **DL / MDL** | Detection Limit. The smallest amount the method can reliably see. |
| **Censored value** | A result reported as `<0.01` — below the detection limit. |
| **g/t** | Grams per tonne, the standard unit for gold grade. Numerically identical to ppm. |
| **oz/t** | Troy ounces per short ton. Exactly 240/7 g/t. |
| **ICP** | Inductively Coupled Plasma — the instrument family used for multi-element geochemistry. |
| **Aqua regia / four-acid** | Partial and near-total digests. A low result by aqua regia is not the same statement as a low result by four-acid. |
| **CoA** | Certificate of Analysis. The lab's formal, signed statement of results. |
| **TAT** | Turnaround time. What clients phone about. |
| **NI 43-101** | The Canadian regulation governing disclosure of mineral project information. Why the audit trail is not optional. |

---

## 3. The principles

### 3.1 A result is not a float

`<0.01 g/t` is not `0`, not `0.01`, and not `null`. It is the statement "the
method could not see it." Flattening that on the way in makes every downstream
mean and composite silently wrong, and the substitution becomes invisible.

`MeasuredValue` (`domain/values.py`) has no `__float__`, no `__int__`, and no
ordering against plain numbers. Code that needs a number must call
`require_detected()` — which raises, surfacing the ambiguity — or
`substituted(strategy)`, which forces you to name the convention. Either way the
choice appears in the diff.

### 3.2 Arithmetic is Decimal, and conversion is explicit

Never `float`. A certificate must be reproducible bit-for-bit years later, and
binary floats are not reproducible across platforms in the last digits.

Conversion factors are exact rationals, not pre-divided decimals: one oz/t is
exactly 240/7 g/t, and storing `34.2857142857` would be a lie that compounds
over a year of data. Conversion runs in a pinned decimal context so a result
never depends on the calling process's precision.

Converting across dimensions is **refused**. Milligrams of bead into grams per
tonne needs the sample weight, so it is a calculation with a named input
(`domain/assay.py`), not a unit conversion.

### 3.3 The database enforces what matters, not the service layer

The application connects as `msa_app`, which holds no UPDATE or DELETE on
`audit_event`. Migrations connect as the schema owner, because an owner bypasses
grants and therefore could never be constrained by them — that is why
`database_url` and `migration_database_url` are separate settings.

The same reasoning puts the amendment-reason rule in a CHECK constraint rather
than in a service method. A corrected result with no stated reason is the most
common finding in a laboratory audit; it should be impossible, not discouraged.

**When you add a table, you must grant on it explicitly.** There is no `ALTER
DEFAULT PRIVILEGES`. This is deliberate: it forces the mutable-or-append-only
decision into a reviewable diff.

### 3.4 The domain core is pure

Everything in `domain/` has no session, no clock, no I/O. `lifecycle.py` knows
what a transition *requires*, not how to perform one, so the whole authorisation
model is a table of inputs and expected refusals. Services perform the move;
the domain decides whether they may.

### 3.5 Refusals explain themselves

Error messages are written for the person who hit them, and they name the
remedy. Compare:

```
a sample cannot go from received to ready_for_assay
```

with what `lifecycle.py` actually says:

```
a sample cannot go from received to ready_for_assay; only a pulp may
skip preparation, and this is core
```

Status codes carry meaning too: **403** means find someone with authority,
**409** means the thing moved under you, **422** means finish the paperwork.
Collapsing them to 400 throws that away.

### 3.6 Absence is never rendered as zero

Inherited from QC Sentinel, and it applies here too. A sample with no result has
no grade — not `0.00`. A submission with no samples received has a null average,
not `0`. Every rate carries its denominator.

---

## 4. How to add things

### Adding a table

1. Add the model to `db/models.py`. Enums go through the `_enum()` helper.
2. `make revision m="what it is"`, then **read the generated migration**.
   Autogenerate does not know about grants, triggers, or data.
3. Add the table to `MUTABLE_TABLES` or `APPEND_ONLY_TABLES` in a new grants
   migration. It has no privileges until you do.
4. `make migrate`, then `make check`.

### Adding a domain rule

Write it in `domain/` as a pure function or dataclass, with an example-based
test for the cases you thought of and a Hypothesis property for the law it rests
on. If it needs a session or a clock, it belongs in a service, not the domain.

### Adding an endpoint

The route translates HTTP to a service call and back. Business rules live in
`domain/`; orchestration and persistence live in services. If a route contains
an `if` about laboratory policy, it is in the wrong file.

New domain exceptions go in the `_ERROR_STATUS` map in `web/app.py`, mapped to
the code that means what they mean.

---

## 5. Testing

| Suite | What it is for | Needs services |
|---|---|---|
| `tests/unit` | The cases you thought of, and every refusal path | No |
| `tests/property` | The laws the domain rests on, over generated inputs | No |
| `tests/integration` | Grants, constraints, and anything the database decides | Postgres |

Integration tests run against a dedicated `msa_test` database, created and
migrated on first use, each test inside a rolled-back transaction, bound to the
**restricted application role**. A service that needs a forbidden grant fails
there rather than in deployment. Everything skips cleanly when Postgres is
unreachable.

The rule for property tests: bound your strategies to what a laboratory actually
produces. Unbounded `Decimal` explores exponents no balance can generate and
teaches you nothing.

---

## 6. Known-unresolved questions

These are open on purpose. Do not resolve them by picking something and moving
on — find out.

- **Balance sensitivity** is currently a parameter every caller must supply. It
  should come from the instrument record once calibration data is modelled.
- **Is silver reported on every fire assay, or on request?** Drives whether
  `silver_by_difference` runs eagerly or on demand.
- **Submission numbering** (`SUB-2026-0841`) is invented and needs the real
  convention before it hardens into stored data.
- **Can a sample move between submissions?** If re-submission happens it is a
  chain, not an update to `submission_id`.
- **The sample label grammar** in `sample_id.py` is modelled on one convention.
  Other clients will differ, and the resolution is probably per-client parsing
  rules as data — the same shape as Sentinel's `identification.py` — not more
  regexes.
