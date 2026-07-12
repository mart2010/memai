# Memai Specification

This directory is the **canonical specification** of Memai — what the system does
(functional) and how it is built (technical). It exists to keep three artefacts
continuously aligned:

```
        ┌─────────────┐
        │    SPEC     │  what the system must do
        └──────┬──────┘
     defines   │   ▲  adapts to deliberate
     expected  │   │  design changes
     behaviour ▼   │
        ┌──────────┴──┐
        │    CODE     │  what the system does
        └──────┬──────┘
     verified  │   ▲
     against   ▼   │
        ┌─────────────┐
        │    TESTS    │  proof that code conforms to spec
        └─────────────┘
```

- A **spec change** (new requirement, changed behaviour) drives code changes, and the
  tests that cite the affected IDs must be updated in the same change.
- A **code change** that alters externally observable behaviour or a stated invariant
  requires a spec adaptation in the same commit — the spec never trails the code.
- **Tests are the conformance link**: a test citing a requirement ID is the evidence
  that the code satisfies it. A requirement without a citing test is unverified (which
  is allowed, but visible — see Conformance below).

## Documents

| Document | Contents |
|---|---|
| [GLOSSARY.md](GLOSSARY.md) | The ubiquitous language: voice-pipeline technical terms, voice-assistant domain terms, and Memai's own domain terms. **Read first** — every other document uses these terms without redefining them. |
| [FUNCTIONAL.md](FUNCTIONAL.md) | Functional requirements (`FR-…`): externally observable behaviour, organised by capability. |
| [TECHNICAL.md](TECHNICAL.md) | Technical requirements (`TR-…`) and invariants (`INV-…`): architecture, protocol, data model, algorithms, formats. |

## Requirement IDs

Every normative statement carries a stable ID:

| Prefix | Meaning | Lives in |
|---|---|---|
| `FR-nnn` | Functional requirement — observable behaviour | FUNCTIONAL.md |
| `TR-nnn` | Technical requirement — internal contract, format, algorithm | TECHNICAL.md |
| `INV-nn` | Invariant — cross-cutting hard rule; violating one is always a defect | TECHNICAL.md §Invariants |

Rules:

1. **IDs are never renumbered or reused.** Numbering within a section is blocked by
   capability (e.g. FR-1xx = live conversation) purely for readability; gaps are fine.
2. **A retired requirement keeps its ID**, marked `[RETIRED yyyy-mm-dd — reason/replacement]`.
   Its citing tests are updated or removed in the same change.
3. **Wording**: *must* = mandatory (test-worthy), *should* = strong default (deviation
   needs a stated reason), *may* = permitted. Statements without these verbs are
   descriptive context, not requirements.
4. A requirement marked **⚠ Gap** describes intended behaviour whose wiring is
   incomplete in code; the gap text says exactly what is missing. Gaps are tracked
   honestly rather than specced as if done.

## Conformance: how tests cite the spec

- A test that verifies a requirement cites the ID in its docstring, e.g.:

  ```python
  def test_bundle_items_install_as_unseen():
      """Spec: INV-12, FR-602 — a bundle can never claim the user knows an item."""
  ```

- Citations are greppable both ways:
  - "what verifies FR-602?" → `grep -rn "FR-602" server/tests client/tests setup/tests`
  - "is this test still justified?" → the docstring names its requirements.
- One test may cite several IDs; one ID may be cited by several tests. The tightest
  mapping is test-per-invariant for `INV-…`.
- Existing tests are back-filled with citations opportunistically — whenever a test is
  touched, its citation is added. New tests cite from day one.

## The alignment loop in practice

**When changing the spec** (design decision):
1. Edit the requirement (or add one; retire what it replaces).
2. Implement the code change.
3. Add/update tests citing the ID.
4. One commit (or PR) carries all three.

**When changing code:**
1. Before committing, ask: does this change observable behaviour, a format, a
   threshold, or an invariant? If yes → update the affected requirement(s) in the same
   commit. `grep docs/spec -rn "<the thing you changed>"` finds them.
2. If the change contradicts an `INV-…`: stop — that is a design discussion, not a
   patch (same rule as CLAUDE.md's design-integrity clause).

**When a test fails against unchanged spec:** the code is wrong — fix the code, not
the test or the spec.

**Periodic drift review** (e.g. at each phase end): sweep the spec against the code the
same way stale docs were swept — every number, threshold, message type, and behaviour
claim in the spec must be traceable to current code. The spec carries a *Last verified
against code* date in each document header for this purpose.

## Relationship to other documents

- **CLAUDE.md** keeps session working rules (toolchain, testing style, hard reminders)
  and points here for all behavioural/architectural facts. Where the two disagree, the
  spec wins — and the disagreement is a bug to fix immediately.
- **docs/PLAN.md** is the historical phase log (what was built when, findings). The
  spec is the present tense; PLAN.md is the past tense.
- **docs/BRIEF_*.md** (and `docs/archive/`) record design rationale — the *why* behind
  requirements. The spec states the *what*; a requirement may point at a brief for
  rationale but must stand alone.
- **README/docs sub-pages** are marketing/user-facing derivatives; they must never
  contradict the spec.
