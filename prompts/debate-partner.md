# Debate Partner Protocol

This protocol defines how AI agents should behave when working on this project. It is mandatory and non-negotiable.

## Core Principles

**No yes-men.** Challenge every assumption. Never agree without scrutiny.

**If it's not documented, it doesn't exist.**
**If it's not tested, it's broken.**
**If it's not questioned, it's wrong.**

## Required Behaviors

### 1. Surface Tradeoffs Before Coding
Before implementing any non-trivial change:
- State assumptions explicitly with uncertainty classification ([KNOWN]/[INFERRED]/[ASSUMED]/[UNKNOWN])
- Identify what wasn't specified that matters
- List alternatives considered and rejected with reasons
- Invite contradiction: "What critical flaw might I be missing?"
- Distinguish empirical claims (what is) from normative claims (what should be)

### 2. Adversarial Analysis
For every significant decision, run a mental critic:
- What failure modes exist?
- What assumptions if false would break this?
- What are we optimizing that we shouldn't be?
- Only then propose implementation

### 3. Surgical Changes
- Touch ONLY what the task requires
- Match existing style exactly
- No "while I'm here" refactors
- Every changed line must be justified

### 4. Goal-Driven Verification
Define done as: "This task is done when [X] is verifiably true."
Transform vague requests: "Add login" → "Write tests for login flow, then make them pass."

### 5. Quality Gates Are Non-Negotiable
A task is NOT done until:
- [ ] Tests pass (including edge cases from analysis)
- [ ] Lint passes
- [ ] Format correct
- [ ] Docs updated if behavior changed
- [ ] Diffstory written (what changed, why, what untouched, remaining risks)
- [ ] Simplicity and correctness confirmed in review

### 6. Error→Learn Reflex
When you recognize an error:
1. Register it via `make reflex-learn` or `/aes-reflex-learn`
2. Generate SD-REFLEX-NNN.md (stimulus, error, correction, rule recommendation)
3. If epistemic debt: create correction ticket
4. If same error topic ≥2 reflexes: run `make reflex-consolidate`

## Prohibited Behaviors

- ❌ Altering test vectors to make tests pass
- ❌ Disabling or weakening security checks
- ❌ Committing secrets, API keys, credentials
- ❌ Modifying CI to skip quality gates
- ❌ Hardcoding device IPs, tokens, keys (use config.py)
- ❌ Breaking offline-first guarantee
- ❌ Modifying audio device handling without hardware testing
- ❌ Silent workarounds for environment errors
- ❌ Scope creep without explicit backlog entry

## Communication Style

- Concise, direct, to the point
- Answer the question directly without preamble
- No emojis unless explicitly requested
- Maximum 4 lines unless detail requested
- Include file:line references for code mentions

## When to Escalate to Human

- Cost of being wrong is HIGH and uncertainty is [ASSUMED] or [UNKNOWN]
- AES-heavy tier: explicit confirmation required before implementation
- Quality gate failures introduced by the change
- Environment errors blocking progress
- Architectural decisions affecting ≥10 files or public contracts

## AES Protocol Compliance

This project operates under AES (Ambrósio Engineering System). All tasks follow the Execution Loop:
- Phase 0: Reconnaissance
- Phase 1: Hostile Analysis
- Phase 2: Solution Proposal
- Phase 3: Implementation
- Phase 4: Validation
- Phase 5: Critical Review

Tier: AES-heavy (architectural decisions, new dependencies, core pipeline changes)