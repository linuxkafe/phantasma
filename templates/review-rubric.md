# Review rubric — guest access (T068) and maintenance automation

Pre-registered **before** any reviewer ran. A rubric written after the findings
exist is a rubric fitted to the findings.

Scope under review: `f0636a0~1..9415f99` (13 commits, 21 files, +3013/-59).

## What this change claims to be

Two things, from two unrelated problems:

1. **Guest authorisation on Discord.** A guest has no login and no session; they
   exist only on Discord, identity is `message.author.id`. An allowlist of skills,
   a daily quota, ids configurable by the owner, admin UI. The house's devices are
   never reachable by a guest.
2. **Maintenance automation.** A dependency check that proves each host actually
   generates, container image updates with rollback, model updates with a digest
   ledger, and a nightly run at 03:30.

## Dimensions

Every finding MUST name one of these. A finding that fits none is not a finding
against this rubric.

### R1 — Fail mode correctness
When a decision goes wrong, does the system fail safe or fail open? Specifically:
unwired resolvers, unreadable config, missing stores, unparseable ids, empty
allowlists. The security-relevant claim is that a guest cannot reach a device.

Closure must be a mechanical statement: a command whose output changes.

### R2 — Evidence quality
Does every claim rest on something reproducible? A test that cannot fail is not
evidence. A comment saying "this is why" is not a measurement. Grepping served HTML
for a CSS string is not verification of layout.

### R3 — Falsifiability
Was each behavioural claim falsified before it was committed? A test whose
mutation still passes is decoration.

### R4 — Blast radius of failure
What breaks when this is wrong, and who notices? Includes: what a wrong deploy
does to a running service, what a wrong permission does to the house, what a
silent unattended update does at 03:30.

### R5 — Reversibility
When a step goes wrong, can it be undone, by an operator, without guessing? Images,
models, deploys, config changes.

### R6 — Honesty of the record
Do commit messages, comments and docstrings describe what the code does, including
where it was wrong? Does anything claim more than was measured? A comment that
describes an intent rather than the mechanism is a finding here.

### R7 — Scope discipline
Was anything changed that the ticket did not require, and was it justified at the
time? Speculative generality, unused parameters, dead abstraction.

### R8 — Coupling to the environment
Does the change work only where it was built? Hardcoded hosts, interpreters
resolved from `PATH`, absolute paths, assumptions about which venv exists, tests
that only run in one environment.

### R9 — Deferral honesty
Anything left broken or unresolved: is it written down as open, or presented as
done?

## Severity

- **BLOCKER** — ships a security hole, loses data, or takes the assistant down
  with no route to recovery.
- **MAJOR** — wrong behaviour a user or operator will hit, or a claim in the
  record that the code does not support.
- **MINOR** — readability, naming, dead weight, or a gap that has an escape
  hatch.

## Reviewer independence

Four personas, fresh sessions, no shared findings and no access to this file
before they submit: Cínico (attacks claims), Purista (attacks correctness and
fail modes), Pragmático (attacks operability and maintenance), Utilizador (asks
what actually happens to the person living with this).