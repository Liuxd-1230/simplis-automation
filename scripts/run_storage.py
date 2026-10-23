from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from simplis_automation_v2.run_storage import *

if __name__ == "__main__":
    raise SystemExit(main())
