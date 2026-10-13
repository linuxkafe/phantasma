"""Which trained model belongs to whom, and what the assistant should load.

One JSON file, one row per enrolled person. Not a column on `users`, because
what is being recorded is not a property of the account -- it is a property of
a set of files on disk, and those files can be deleted, re-imported or
replaced without the account changing.

The honest limit is in the name of what this does NOT do. It does not identify
anyone. A wake-word model answers "someone said the phrase"; the microphone
hears whoever is in the room, so a person standing next to the owner will wake
the owner's model. This is enrolment. The owner was told that on 2026-10-13
and chose it over speaker-ID, which is a different and much larger piece of
work.
"""

import json
import logging
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = ["WakeWordEntry", "WakeWordRegistry", "new_entry", "slugify"]


def slugify(label: str) -> str:
    """A filesystem-safe handle for a person.

    Lowercase, accents folded, everything else to dashes. Two people called
    "Ana Silva" and "ana silva" collide here on purpose: they are one person to
    the owner, and silently creating two profiles for one voice would leave two
    competing models for the same mouth.
    """
    import unicodedata

    folded = "".join(
        ch for ch in unicodedata.normalize("NFD", (label or "").lower())
        if unicodedata.category(ch) != "Mn"
    )
    slug = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    return slug or "utilizador"


@dataclass
class WakeWordEntry:
    """One enrolled person.

    Attributes:
        label: The name the owner typed. Never used as a path.
        slug: Filesystem handle, derived from the label.
        model_path: Absolute path to the trained `.onnx`. None while the
            person is enrolled but has not yet returned a model from the Colab.
        phrase: The wake word, if written down.
        trained_at: When the model came back.
        sample_count: How many positives went into it, carried forward from the
            bundle so the wizard can say "trained from 87 clips".
        false_positive_rate: Always None unless the owner measured it. There is
            no default of 0, because a default of 0 would be read as a
            measurement.
        note: Free text the owner wants to keep with the entry.
    """

    label: str
    slug: str
    model_path: Optional[str] = None
    phrase: str = ""
    trained_at: str = ""
    sample_count: int = 0
    false_positive_rate: Optional[float] = None
    note: str = field(default="", repr=False)


class WakeWordRegistry:
    """The enrolled people, on disk, safe across restarts and concurrent writers."""

    def __init__(self, path: str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._entries: list[WakeWordEntry] = []
        self.load()

    def load(self) -> list[WakeWordEntry]:
        """Re-read from disk. A missing file is a first boot, not an error."""
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except FileNotFoundError:
                self._entries = []
                return []
            except (OSError, ValueError) as exc:
                logger.error("Registo de palavras de acordar ilegivel em %s: %s. "
                             "A tratar como vazio.", self.path, exc)
                self._entries = []
                return []

            entries = []
            for row in raw if isinstance(raw, list) else []:
                if not isinstance(row, dict) or not row.get("slug"):
                    continue
                entries.append(WakeWordEntry(
                    label=str(row.get("label") or row["slug"]),
                    slug=str(row["slug"]),
                    model_path=row.get("model_path") or None,
                    phrase=str(row.get("phrase") or ""),
                    trained_at=str(row.get("trained_at") or ""),
                    sample_count=int(row.get("sample_count") or 0),
                    # `is None` and not `or`, so a measured 0.0 survives.
                    false_positive_rate=(
                        row.get("false_positive_rate")
                        if row.get("false_positive_rate") is not None else None),
                    note=str(row.get("note") or ""),
                ))
            self._entries = entries
            return list(self._entries)

    def _write_locked(self) -> None:
        directory = os.path.dirname(self.path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=directory, prefix=".wakewords-",
                suffix=".tmp", delete=False)
            try:
                with handle:
                    json.dump([asdict(e) for e in self._entries], handle,
                              ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(handle.name, self.path)
            except BaseException:
                try:
                    os.unlink(handle.name)
                except OSError:
                    pass
                raise
        except OSError as exc:
            logger.error("Nao foi possivel gravar o registo em %s: %s",
                         self.path, exc)

    # --- queries ---------------------------------------------------------

    def all(self) -> list[WakeWordEntry]:
        with self._lock:
            return list(self._entries)

    def get(self, slug: str) -> Optional[WakeWordEntry]:
        with self._lock:
            return next((e for e in self._entries if e.slug == slug), None)

    def enrolled(self) -> list[WakeWordEntry]:
        """Only those with a model actually on disk.

        The distinction matters at load time: a person who started enrolling and
        never finished has an entry and no model, and pointing `HotwordDetector`
        at a missing `.onnx` is exactly the failure `scripts/check-wakewords.sh`
        exists to catch before the container starts.
        """
        return [e for e in self.all()
                if e.model_path and os.path.isfile(e.model_path)]

    def model_paths(self) -> list[str]:
        """Absolute paths for every installed per-user model.

        This is what gets appended to `WAKEWORD_MODELS`. Existing entries are
        kept -- the registry ADDS trained voices, it does not replace the
        default ones, unless the owner removes them.
        """
        return [e.model_path for e in self.enrolled() if e.model_path]

    # --- mutations -------------------------------------------------------

    def upsert(self, entry: WakeWordEntry) -> WakeWordEntry:
        with self._lock:
            for position, existing in enumerate(self._entries):
                if existing.slug == entry.slug:
                    self._entries[position] = entry
                    self._write_locked()
                    return entry
            self._entries.append(entry)
            self._write_locked()
            return entry

    def remove(self, slug: str) -> bool:
        """Forget a person. The file on disk is NOT deleted.

        Deliberate: deleting somebody's trained voice is irreversible and this
        is reachable from a web page. The caller can delete the file.
        """
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.slug != slug]
            if len(self._entries) == before:
                return False
            self._write_locked()
            return True

    def set_false_positive_rate(self, slug: str, rate: float) -> bool:
        """Record what the owner measured.

        Only way this field is ever non-None: someone ran the room test and
        typed in the result.
        """
        with self._lock:
            entry = next((e for e in self._entries if e.slug == slug), None)
            if entry is None:
                return False
            entry.false_positive_rate = float(rate)
            self._write_locked()
            return True


def new_entry(label: str, phrase: str = "") -> WakeWordEntry:
    """A fresh, unsaved enrolment. `model_path` stays None until a model comes
    back from the notebook, and `enrolled()` skips an entry without one."""
    return WakeWordEntry(label=label, slug=slugify(label), phrase=phrase,
                         trained_at=datetime.now().isoformat(timespec="seconds"))
