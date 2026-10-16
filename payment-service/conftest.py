import os
import sys
from pathlib import Path

# Make the service root importable (api, core, and the `shared` symlink all live here).
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "test_settings")
