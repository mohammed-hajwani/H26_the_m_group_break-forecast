# /// script
# requires-python = ">=3.11"
# dependencies = ["packaging>=24.0", "jsonschema>=4.21", "uv>=0.5"]
# ///
"""Entry point for the break-forecast skill.

`uv run scripts/bf.py <command> ...` installs the dependencies above automatically and runs
the CLI from this checkout, so the skill works without `pip install`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from break_forecast.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
