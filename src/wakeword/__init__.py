"""Per-user wake-word enrolment (T071).

Three separated pieces, because they fail in different ways and are fixed by
different people:

  * `dataset`  -- validates recorded clips and refuses to build a training
                  bundle from evidence that cannot produce a usable model.
  * `registry` -- who is enrolled and which `.onnx` is theirs.
  * `importer` -- accepts a model back from the Colab, and refuses to install
                  one that does not load.

The TRAINING is not here and never will be by default: it runs in the owner's
Google Colab, by the owner's decision on 2026-10-13, recorded in CLAUDE.md as a
deliberate exception to the offline-first non-goal. What stays local is
inference, and it stays local because `HotwordDetector` loads whatever path it
is given and runs onnxruntime.
"""

from src.wakeword.dataset import Sample, SampleCheck, build_bundle, validate_samples
from src.wakeword.importer import ImportResult, import_model, remove_model, validate_onnx
from src.wakeword.registry import WakeWordEntry, WakeWordRegistry, new_entry, slugify

__all__ = ["Sample", "SampleCheck", "build_bundle", "validate_samples",
           "WakeWordEntry", "WakeWordRegistry", "new_entry", "slugify",
           "ImportResult", "import_model", "remove_model", "validate_onnx"]
