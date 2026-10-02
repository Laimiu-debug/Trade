"""Export saved reviews/statistics through the local application without network services."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from trade_app.reviews.export_cli import main

if __name__ == '__main__':
    raise SystemExit(main())
