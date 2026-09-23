"""Main application entry point for pHantasma.

Thin wrapper that delegates to assistant.run().
All pipeline logic is in assistant.py for testability.
"""

import sys

from assistant import run


def main() -> int:
    """Run the pHantasma voice assistant.

    Returns:
        Exit code: 0 on clean shutdown, 1 on startup failure.
    """
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
