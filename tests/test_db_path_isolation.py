"""Regression tests for the dev/prod database entanglement.

The bug this prevents: ``src/api/admin.py`` and ``src/api/memory_graph.py``
hardcoded ``/opt/phantasma/data/*.db``. In production that was correct by
coincidence. In a dev checkout it meant a dev process opened -- and wrote to --
the PRODUCTION database. Nothing failed, no warning was printed, and the damage
was only discoverable afterwards by looking at the data.

The invariant now: every database path in the tree resolves through
``config``, and a dev checkout resolves to its OWN data directory. These tests
encode that so it cannot silently regress -- reintroducing a literal in any
module has to make this file fail.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Modules that must never carry a host-specific database path again.
API_MODULES = [
    REPO / "src" / "api" / "admin.py",
    REPO / "src" / "api" / "memory_graph.py",
]

# Directories scanned for hardcoded host paths in first-party code.
CODE_DIRS = [REPO / "src", REPO / "skills"]

# A path under /opt/<something> that is a data file (db/json/wav/onnx).
HOST_DATA_PATH = re.compile(r'["\']/opt/[^"\']*\.(?:db|json|sqlite3?|wav|onnx)["\']')


class TestNoHardcodedDataPaths:
    @pytest.mark.parametrize("mod", API_MODULES, ids=lambda p: p.name)
    def test_api_module_has_no_literal_db_path(self, mod):
        src = mod.read_text(encoding="utf-8")
        offenders = [
            line.strip()
            for line in src.splitlines()
            if HOST_DATA_PATH.search(line) and not line.strip().startswith("#")
        ]
        assert not offenders, (
            f"{mod.name} hardcodes a host data path: {offenders}. "
            f"A dev process would then open the PRODUCTION database. "
            f"Use config.CONFIG_DB_PATH / config.BRAIN_DB_PATH."
        )

    def test_api_modules_resolve_through_config(self):
        """The paths must come from config, not merely be absent."""
        admin = (REPO / "src" / "api" / "admin.py").read_text(encoding="utf-8")
        mg = (REPO / "src" / "api" / "memory_graph.py").read_text(encoding="utf-8")
        assert "config.CONFIG_DB_PATH" in admin
        assert "config.BRAIN_DB_PATH" in admin
        assert "config.BRAIN_DB_PATH" in mg

    def test_first_party_code_has_no_literal_host_data_path(self):
        """Broad sweep: no first-party source file may hardcode a host data file.

        The narrower tests above only cover the two modules that were broken.
        This one catches the next one, in skills/ or wherever it shows up.
        """
        offenders = []
        for d in CODE_DIRS:
            if not d.exists():
                continue
            for f in d.rglob("*.py"):
                if "venv" in f.parts or ".venv" in f.parts:
                    continue
                for i, line in enumerate(
                    f.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                ):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if HOST_DATA_PATH.search(stripped):
                        offenders.append(f"{f.relative_to(REPO)}:{i}: {stripped[:90]}")
        assert not offenders, "hardcoded host data paths:\n" + "\n".join(offenders)


class TestDbPathResolution:
    def test_db_path_makes_relative_absolute_against_repo_root(self):
        """A relative .env value must not depend on the process cwd."""
        import config as cfgmod

        cfg = cfgmod.Config()
        out = cfg.db_path("data/brain.db")
        assert Path(out).is_absolute()
        assert out.startswith(str(REPO))

    def test_db_path_preserves_absolute_input(self):
        """A host may still place its databases outside the tree."""
        import config as cfgmod

        cfg = cfgmod.Config()
        out = cfg.db_path("/var/lib/phantasma/brain.db")
        assert out == "/var/lib/phantasma/brain.db"

    def test_db_path_is_idempotent(self):
        import config as cfgmod

        cfg = cfgmod.Config()
        once = cfg.db_path("data/brain.db")
        assert cfg.db_path(once) == once

    def test_config_exposes_config_db_path(self):
        import config as cfgmod

        assert hasattr(cfgmod.Config(), "config_db_path")
        assert cfgmod.CONFIG_DB_PATH
        assert Path(cfgmod.CONFIG_DB_PATH).is_absolute()


class TestNoCwdDependence:
    def test_config_load_is_cwd_independent(self):
        """Loading config from an unrelated cwd must still resolve to the repo.

        This is the specific way the old relative values bit: start the process
        somewhere else and ``data/brain.db`` silently means something else.
        """
        import os
        import subprocess
        import sys
        import tempfile

        code = (
            "from dotenv import load_dotenv; load_dotenv(); import config, json;"
            "print(json.dumps({'b': config.config.brain_db_path,"
            "'c': config.config.config_db_path}))"
        )
        env = dict(os.environ, PYTHONPATH=str(REPO))
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run(
                [sys.executable, "-c", code],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                timeout=90,
            )
        import json

        assert out.returncode == 0, out.stderr[-500:]
        payload = json.loads(out.stdout.strip().splitlines()[-1])
        assert Path(payload["b"]).is_absolute()
        assert str(payload["b"]).startswith(str(REPO))
        assert Path(payload["c"]).is_absolute()
        assert str(payload["c"]).startswith(str(REPO))


class TestNoCyclicImport:
    @pytest.mark.parametrize("mod", API_MODULES, ids=lambda p: p.name)
    def test_api_module_imports_cleanly(self, mod):
        """Importing config from an API module must not create a cycle."""
        import os
        import subprocess
        import sys

        rel = mod.relative_to(REPO).with_suffix("")
        dotted = ".".join(rel.parts)
        env = dict(os.environ, PYTHONPATH=str(REPO))
        out = subprocess.run(
            [sys.executable, "-c", f"import {dotted}"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert out.returncode == 0, f"{dotted} failed to import:\n{out.stderr[-600:]}"
