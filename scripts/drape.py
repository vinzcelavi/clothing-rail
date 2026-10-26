"""Settle the flat-lay garment on its hanger with a cloth sim; writes build/textures/drape.npz.

Run: blender -b --factory-startup -P scripts/drape.py -- [config/rail.json]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rail import drape  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
drape.run(args[0] if args else None)
