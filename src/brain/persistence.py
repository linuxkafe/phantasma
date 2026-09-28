"""FlyBrain state persistence to SQLite."""

import io
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import msgpack
import numpy as np

if TYPE_CHECKING:
    from src.brain.fly_brain import FlyBrain

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
TABLE_NAME = "flybrain_state"


def _array_to_bytes(arr: np.ndarray) -> bytes:
    """Serialize numpy array to bytes using np.save."""
    buf = io.BytesIO()
    np.save(buf, arr, allow_pickle=False)
    return buf.getvalue()


def _bytes_to_array(data: bytes) -> np.ndarray:
    """Deserialize numpy array from bytes using np.load."""
    buf = io.BytesIO(data)
    return np.load(buf, allow_pickle=False)


class FlyBrainStore:
    """Persist FlyBrain internal state to SQLite.

    Stores full internal state (not just ConnectomeState snapshot) to enable
    full restoration of learned affinities, habituation, and orientation.
    """

    def __init__(self, db_path: str, auto_save_interval: int = 10):
        """Initialize store.

        Args:
            db_path: Path to SQLite database file.
            auto_save_interval: Save every N steps (default 10).
        """
        self.db_path = Path(db_path)
        self.auto_save_interval = auto_save_interval
        self._steps_since_save = 0

        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()

    def _init_db(self) -> None:
        """Create table with schema version if not exists."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    schema_version INTEGER NOT NULL DEFAULT {SCHEMA_VERSION},
                    data BLOB NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()
        logger.info(f"FlyBrain store initialized at {self.db_path}")

    def _serialize_state(self, brain: "FlyBrain") -> bytes:
        """Serialize full FlyBrain state to msgpack bytes."""
        state = {
            "schema_version": SCHEMA_VERSION,
            "steps": brain.steps,
            "ring": {
                "orientation_deg": brain.ring.orientation_deg,
                "activity": _array_to_bytes(brain.ring.activity),
                "n": brain.ring.n,
                "sigma_in": brain.ring.sigma_in,
            },
            "mushroom_body": {
                "n_sensory": brain.mb.n_sensory,
                "n_kc": brain.mb.n_kc,
                "n_mbon": brain.mb.n_mbon,
                "kc_density": float(np.mean(brain.mb._proj > 0)),
                "wkc": _array_to_bytes(brain.mb.wkc),
                "proj": _array_to_bytes(brain.mb._proj),
                "valence": _array_to_bytes(brain.mb.valence),
                "affinity": brain.mb.affinity,
                "kc": _array_to_bytes(brain.mb.kc),
                "mbon": _array_to_bytes(brain.mb.mbon),
                "dan": brain.mb.dan,
            },
            "neuromodulatory_pool": {
                "tau": brain.pool.tau,
                "baseline": brain.pool.baseline,
                "gain": brain.pool.gain,
                "value": brain.pool.value,
                "drive": brain.pool.drive,
            },
            "saved_at": datetime.now().isoformat(),
        }
        return msgpack.packb(state, use_bin_type=True)

    def _deserialize_state(self, data: bytes) -> dict:
        """Deserialize msgpack bytes to state dict and convert bytes to numpy arrays."""
        state = msgpack.unpackb(data, raw=False)
        return self._bytes_to_arrays(state)

    def _bytes_to_arrays(self, obj: Any) -> Any:
        """Recursively convert bytes to numpy arrays in state dict."""
        if isinstance(obj, bytes):
            try:
                return _bytes_to_array(obj)
            except Exception:
                return obj
        elif isinstance(obj, dict):
            return {k: self._bytes_to_arrays(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._bytes_to_arrays(v) for v in obj]
        return obj

    def save(self, brain: "FlyBrain") -> None:
        """Persist full FlyBrain state to database (upsert single row)."""
        try:
            serialized = self._serialize_state(brain)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    f"""
                    INSERT OR REPLACE INTO {TABLE_NAME}
                    (id, schema_version, data, updated_at)
                    VALUES (1, ?, ?, ?)
                    """,
                    (SCHEMA_VERSION, serialized, datetime.now().isoformat()),
                )
                conn.commit()
            logger.debug("FlyBrain state saved")
        except Exception as e:
            logger.error(f"Failed to save FlyBrain state: {e}")

    def load(self) -> Optional[dict]:
        """Load latest FlyBrain state from database.

        Returns:
            Deserialized state dict, or None if no state found or on error.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(f"SELECT data, schema_version FROM {TABLE_NAME} WHERE id = 1")
                row = cursor.fetchone()

            if not row:
                logger.info("No FlyBrain state found in database")
                return None

            data, schema_version = row

            if schema_version != SCHEMA_VERSION:
                logger.warning(
                    f"Schema version mismatch: DB={schema_version}, code={SCHEMA_VERSION}"
                )

            state = self._deserialize_state(data)
            logger.info(f"FlyBrain state loaded (steps={state.get('steps', 0)})")
            return state

        except Exception as e:
            logger.error(f"Failed to load FlyBrain state: {e}")
            return None

    def maybe_auto_save(self, brain: "FlyBrain") -> None:
        """Save state if auto-save interval reached."""
        self._steps_since_save += 1
        if self._steps_since_save >= self.auto_save_interval:
            self.save(brain)
            self._steps_since_save = 0
