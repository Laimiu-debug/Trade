"""Source entry point for the new independent strategy laboratory."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from trade_app.research.lab_cli import main

if __name__ == '__main__':
    raise SystemExit(main())
