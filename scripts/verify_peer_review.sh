#!/usr/bin/env bash
# Validate the BLOCKERs from the peer review of f0636a0~1..9415f99.
#
# Run this yourself. The author ran the review and should not be the one
# confirming its conclusions.
#
# Every check below currently FAILS or reports BROKEN. That is the point: each
# one is a reproduced defect, not a prediction. When you have fixed them, the
# same script prints OK.
#
#   bash scripts/verify_peer_review.sh
#
# Exit 0 only if every defect is fixed.
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PY=venv/bin/python
PROD=/opt/phantasma
fails=0

hdr() { printf '\n=== %s ===\n' "$1"; }
ok()  { printf '  OK      %s\n' "$1"; }
bad() { printf '  BROKEN  %s\n' "$1"; fails=$((fails+1)); }

hdr "BLOCKER 1 -- the skill resolver is empty, so the allowlist is not enforced"
# Read PRODUCTION's copy, because that is what answers the guests. Checking the
# dev tree here reported the fix as missing after it had already been deployed,
# which is the same class of mistake in the other direction: a check that reports
# on the wrong tree.
out=$(cd "$PROD" && ./venv/bin/python -c "
import config
from skills.loader import SkillLoader
l = SkillLoader(skills_dir=config.SKILLS_DIR)
l.load_all()
print(len(l.skills))
" 2>/dev/null | tail -1)
if [ "${out:-0}" -gt 0 ]; then
  ok "production's resolver loaded $out skills"
else
  bad "resolver is empty ($out skills) -> matching==[] -> guest passes the gate"
  printf '          FIX: skills/skill_discord.py init_skill_daemon must call load_all()\n'
fi

matched=$($PY -c "
import config
from skills.loader import SkillLoader
l = SkillLoader(skills_dir=config.SKILLS_DIR)
l.load_all()
print(','.join(l.resolve_matching_skills('acende a luz da sala')))
" 2>/dev/null | tail -1)
if [ -n "$matched" ] && [ "$matched" != "[]" ]; then
  ok "a device prompt does resolve skills: $matched"
else
  bad "'acende a luz da sala' matches nothing ($matched)"
fi

if grep -q 'load_all()' "$PROD/skills/skill_discord.py" 2>/dev/null; then
  ok "production's skill_discord calls load_all()"
else
  bad "production's skill_discord does NOT call load_all() -- the resolver is empty there"
fi

hdr "BLOCKER 2 -- the nightly image update has never run"
# NOT "does /opt/phantasma/docker-compose.yml exist". It should NOT: the running
# containers' bind mounts resolve inside the dev tree, where the models are, and
# deploying the compose file to $PROD would point those relative paths at an
# empty directory and bring Ollama up with no models at all.
#
# What has to be true is that the script resolves the file the containers were
# actually created from -- `docker inspect` names it -- so the run does not exit 2.
compose_dir=$(docker inspect ollama --format \
  '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' 2>/dev/null || true)
if [ -z "$compose_dir" ]; then
  bad "could not read the compose project working_dir from the running container"
elif [ -f "$compose_dir/docker-compose.yml" ]; then
  ok "compose resolves to $compose_dir (where the containers came from)"
else
  bad "compose dir $compose_dir has no docker-compose.yml"
fi
if grep -q 'PHANTASMA_COMPOSE_DIR' "$PROD/scripts/update_containers.sh" 2>/dev/null; then
  ok "update_containers.sh resolves the compose dir instead of assuming \$ROOT"
else
  bad "update_containers.sh still assumes \$ROOT/docker-compose.yml -> exits 2 nightly"
fi

hdr "BLOCKER 3 -- the owner cannot revoke a guest through the page"
out=$($PY - <<'PYEOF' 2>/dev/null | tail -1
import sqlite3, sys, tempfile, pathlib
sys.path.insert(0, '.')
db = str(pathlib.Path(tempfile.mkdtemp()) / 'c.db')
con = sqlite3.connect(db)
con.executescript(pathlib.Path('tests/schema.sql').read_text())
con.commit(); con.close()
import config
from src.api import admin as A, discord_access as da
from src import settings_store as ss
A.get_db_connection = lambda: sqlite3.connect(db)
ss.get_setting = lambda k, d=None: ("" if k == "DISCORD_STANDARD_USERS" else d)
config.DISCORD_STANDARD_USERS = [111]
# Compare as TEXT. `_owner_guest_ids()` returns strings and `check()` compares
# `str(user_id)`, so an int probe reports a clean revocation while the id is
# still in force. The first version of this check did exactly that and printed
# OK over the bug it was written to catch.
print("REVOKED" if "111" not in da._owner_guest_ids() else "STILL_ALLOWED")
PYEOF
)
if [ "$out" = "REVOKED" ]; then
  ok "emptying the page field removes the id"
else
  bad "emptying the page field leaves the .env id in force (STILL_ALLOWED)"
  printf '          FIX: the id lists need the empty-is-a-value treatment the\n'
  printf '               allowlist already has (discord_access._owner_list)\n'
fi

hdr "MAJOR 4 -- a broken settings store restores the default allowlist"
out=$($PY - <<'PYEOF' 2>/dev/null | tail -1
import sqlite3, sys
sys.path.insert(0, '.')
import config
from src import settings_store as ss
from src.api import discord_access as da
def boom(k, d=None):
    if k == "GUEST_SKILLS_ALLOWED":
        raise sqlite3.OperationalError("database is locked")
    return d
ss.get_setting = boom
config.GUEST_SKILLS_ALLOWED = None
print(",".join(da.allowed_guest_skills()) or "NONE")
PYEOF
)
if [ "$out" = "NONE" ]; then
  ok "a locked store yields no skills (fail closed)"
else
  bad "a locked store yields [$out] -- the owner's revocation is undone"
fi

hdr "MAJOR 5 -- an accented weather question is refused"
out=$($PY - <<'PYEOF' 2>/dev/null | tail -1
import sys
sys.path.insert(0, '.')
import config
from skills.loader import SkillLoader
from src.api import discord_access as da
l = SkillLoader(skills_dir=config.SKILLS_DIR); l.load_all()
config.DISCORD_STANDARD_USERS = [111]
m = l.resolve_matching_skills("como está o tempo")
print(da.check(111, "como está o tempo", m)[0])
PYEOF
)
if [ "$out" = "True" ]; then
  ok "'como está o tempo' is allowed under the default allowlist"
else
  bad "'como está o tempo' is REFUSED for a guest (accented tuya/xiaomi collision)"
fi

hdr "MAJOR 8/9 -- deploy.sh and CLAUDE.md claims"
if grep -q "DEPLOY_SUDO" CLAUDE.md docs/*.md 2>/dev/null; then
  ok "DEPLOY_SUDO is documented"
else
  bad "DEPLOY_SUDO is undocumented outside deploy.sh itself"
fi
if sudo -n service phantasma status >/dev/null 2>&1; then
  printf '  NOTE    passwordless sudo for "service phantasma" EXISTS on this host.\n'
  printf '          The CLAUDE.md claim that sudo -n fails is wrong for this machine.\n'
else
  printf '  NOTE    passwordless sudo is NOT available here.\n'
fi

hdr "MINOR -- dead knobs and a dead git entry"
if grep -q 'os.getenv("PROBE_TIMEOUT' scripts/dependency_check.py; then
  ok "PROBE_TIMEOUT is read from the environment"
else
  bad "PROBE_TIMEOUT_S is hardcoded to 45.0; the 180s the job exports is ignored"
fi
if git status --porcelain | grep -q 'skill_discord'; then
  bad "skills/skill_discord.py has uncommitted work (the print->logger change)"
else
  ok "working tree is clean for skill_discord.py"
fi

printf '\n'
if [ $fails -eq 0 ]; then
  printf 'ALL CLEAR -- every defect above is fixed.\n'
  exit 0
fi
printf '%d defect(s) still open.\n' "$fails"
exit 1