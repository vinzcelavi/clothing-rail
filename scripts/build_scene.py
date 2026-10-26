"""Build build/rail.blend from the config and the prepared textures.

Run: blender -b --factory-startup -P scripts/build_scene.py -- [config/rail.json]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rail import scene  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
scene.build(args[0] if args else None)
