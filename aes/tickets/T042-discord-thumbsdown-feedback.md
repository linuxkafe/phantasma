# T042 — Discord thumbs-down (👎) silently ignored: no FlyBrain reward, no ack

| Field | Value |
|-------|-------|
| Status | done (2026-09-24) |
| Class | defect fix (user-reported, verified AES) |
| Files | skills/skill_discord.py, tests/test_skill_discord.py |

## Symptom (user report, live)
User reacted 👎 (thumbs-down) in Discord — "não obtive feedback". No ack message, no
FlyBrain update. Reproduced deterministically (stubbed handler, no network):

- Scenario A — reaction on the **bot's message** with 👎 → dropped by GUARD3:
  `_REACTION_REWARD` had no 👎 mapping (`reward=None`).
- Scenario B — reaction on the **user's own message** (the reported case:
  "na minha mensagem") → dropped by GUARD2: handler only accepted reactions on
  messages authored by `client.user`.

## Root cause (AES reproduction, two independent silent guards)
1. `_REACTION_REWARD` lacked `"👎"` (only 👍/❤️/🔥/😡/😢 existed) → thumbs-down on a bot
   reply produced `reward=None` and a silent `return` (also no ack/log).
2. `_handle_reaction` guard `reaction.message.author != client.user` only rewarded
   reactions to the bot's own messages; reacting to one's own message (natural in a
   personal-assistant DM) returned silently too.

Both paths burned the reward and produced zero observability — exactly the report.

## Fix
- Add `"👎": -1.0` to `_REACTION_REWARD`.
- Accept reactions on the bot's **and** the reacting user's own messages
  (`author != client.user and author != user` → ignore only third-party messages).
- Extract pure `_reward_for_emoji()` (testable without discord.py) and route the
  handler through it.
- Add diagnostics: log ignored third-party reactions and unmapped emojis.

## Validation
- `make test`: **154 passed** (was 150; +4 new Discord reward tests), 70.24% coverage.
- `make lint`: all checks passed.
- In-container: bind-mount carries the fix; daemon reinitialized;
  `docker exec` confirms `"👎": -1.0` and `_reward_for_emoji` present in
  `/app/skills/skill_discord.py`.

## Remaining risk
- E2E Discord reaction not run here (needs a live reaction + token). The decision
  logic is unit-tested; the async ack path is unchanged and was already exercised
  in earlier sessions (reaction ack). Own-message reactions now reward the brain —
  intent: personal-DM feedback. Watch for accidental self-rewards in shared servers
  before approving (open question for review).