"""Regenerate the documentation baseline from the two sibling source repositories.

Run with a Python environment that can import final-trade's strategy registry:
    python docs/generate_source_inventory.py
Only reads source repositories; writes docs/source-inventory.json.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent.parent


def feature_ids(repo: str, path: str) -> list[str]:
    if repo == "final-trade":
        rules = [
            ("/health", ["F01"]),
            ("/api/screener/b1-run", ["F09"]),
            ("/api/screener", ["F08", "F10"]),
            ("/api/market/trend-leaders", ["F14"]),
            ("/api/market/limit-up-ladder", ["F15"]),
            ("/api/market/sector-capital-flow", ["F16"]),
            ("/api/market/abnormal-movement", ["F17"]),
            ("/api/market/sentiment-valuation", ["F18"]),
            ("/api/market/news", ["F61"]),
            ("/api/stocks/{symbol}/ai-", ["F66"]),
            ("/api/stocks/{symbol}/annotations", ["F12"]),
            ("/api/stocks", ["F05", "F11", "F13"]),
            ("/api/signals/cross-validate", ["F25", "F26"]),
            ("/api/signals/etf-backtests", ["F27"]),
            ("/api/signals", ["F23"]),
            ("/api/strategies", ["F19", "F20"]),
            ("/api/event-judgment", ["F21"]),
            ("/api/backtest/plateau", ["F31", "F32"]),
            ("/api/backtest/tasks", ["F28", "F29", "F30", "F32"]),
            ("/api/backtest/reports", ["F33"]),
            ("/api/backtest/strategy-signals", ["F34"]),
            ("/api/backtest/scan-stock-strategies", ["F34"]),
            ("/api/backtest/run", ["F28", "F29", "F30"]),
            ("/api/sim/orders", ["F36"]),
            ("/api/sim/fills", ["F36", "F40"]),
            ("/api/sim/settle", ["F37"]),
            ("/api/sim/reset", ["F37"]),
            ("/api/sim/config", ["F39"]),
            ("/api/sim/portfolio", ["F38"]),
            ("/api/review/stats", ["F40", "F51"]),
            ("/api/review/daily", ["F52"]),
            ("/api/review/weekly", ["F55"]),
            ("/api/review", ["F59"]),
            ("/api/ai/providers", ["F63"]),
            ("/api/ai/records", ["F66"]),
            ("/api/ai/playbook", ["F65"]),
            ("/api/ai/quick-prompts", ["F65"]),
            ("/api/ai/parameter-proposals", ["F67"]),
            ("/api/ai/usage", ["F68"]),
            ("/api/ai", ["F64"]),
            ("/api/config", ["F02", "F03", "F63"]),
            ("/api/system/storage", ["F74"]),
            ("/api/system/wyckoff-event-store", ["F07"]),
            ("/api/system/sync-market-data", ["F06"]),
        ]
    else:
        rules = [
            ("/api/capital/status", ["F49", "F50"]),
            ("/api/capital/flows", ["F46"]),
            ("/api/capital/snapshots", ["F47"]),
            ("/api/capital/estimate", ["F48"]),
            ("/api/capital/import", ["F47"]),
            ("/api/capital/nav", ["F49"]),
            ("/api/capital/nodes", ["F50"]),
            ("/api/trades/rounds", ["F45", "F57"]),
            ("/api/trades/import", ["F42"]),
            ("/api/trades/pending", ["F43"]),
            ("/api/trades", ["F41", "F44"]),
            ("/api/reviews/daily/{day}/ai-score", ["F53"]),
            ("/api/reviews/daily/{day}/ai-rehearsal", ["F54", "F69"]),
            ("/api/reviews/daily/{day}/ai-review", ["F52", "F69"]),
            ("/api/reviews/daily", ["F52", "F54"]),
            ("/api/reviews/weekly", ["F55", "F69"]),
            ("/api/reviews/monthly", ["F56", "F69"]),
            ("/api/reviews/missing", ["F58"]),
            ("/api/system/shutdown", ["F01"]),
            ("/api/system", ["F74"]),
            ("/api/cards", ["F62"]),
            ("/api/settings/test-ai", ["F63"]),
            ("/api/settings/test-market", ["F03"]),
            ("/api/settings", ["F02", "F44", "F50", "F63"]),
            ("/api/import/json", ["F70"]),
            ("/api/market/search", ["F04"]),
            ("/api/market/lookup", ["F04"]),
            ("/api/market", ["F03", "F05"]),
            ("/api/stats", ["F51"]),
            ("/api/export/pdf", ["F72"]),
            ("/api/export/json", ["F70"]),
            ("/api/export/markdown", ["F71"]),
        ]
    for prefix, ids in rules:
        if path.startswith(prefix):
            return ids
    raise ValueError(f"Unmapped endpoint: {repo} {path}")


def inventory(repo: str) -> dict:
    root = ROOT / repo
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], text=True).strip()
    api = []
    field_models = []
    for file in sorted((root / "backend/app").rglob("*.py")):
        tree = ast.parse(file.read_text(encoding="utf-8-sig"))
        if file.name == "models.py" or "routers" in file.parts:
            for cls in tree.body:
                if not isinstance(cls, ast.ClassDef):
                    continue
                fields = [{"name": item.target.id, "annotation": ast.unparse(item.annotation),
                           "defaultExpression": ast.unparse(item.value) if item.value else None}
                          for item in cls.body if isinstance(item, ast.AnnAssign)
                          and isinstance(item.target, ast.Name)]
                if fields:
                    field_models.append({"name": cls.name, "bases": [ast.unparse(x) for x in cls.bases],
                                         "file": file.relative_to(root).as_posix(), "line": cls.lineno,
                                         "declaredFields": fields})
        prefix = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "APIRouter":
                for keyword in node.keywords:
                    if keyword.arg == "prefix":
                        prefix = ast.literal_eval(keyword.value)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and isinstance(dec.func.value, ast.Name) and dec.func.value.id in ("app", "router")
                        and dec.func.attr in ("get", "post", "put", "patch", "delete", "websocket")
                        and dec.args and isinstance(dec.args[0], ast.Constant)):
                    continue
                path = prefix + dec.args[0].value
                api.append({"method": dec.func.attr.upper(), "path": path,
                            "handler": node.name, "file": file.relative_to(root).as_posix(),
                            "line": node.lineno, "featureIds": feature_ids(repo, path)})
    route_file = root / ("frontend/src/app/routes.tsx" if repo == "final-trade" else "frontend/src/App.tsx")
    routes = re.findall(r'<Route path="([^"]+)"', route_file.read_text(encoding="utf-8-sig"))
    storage_files = []
    for file in sorted((root / "frontend/src").rglob("*")):
        if file.suffix not in (".ts", ".tsx") or "/test/" in file.as_posix():
            continue
        content = file.read_text(encoding="utf-8-sig")
        if "localStorage" in content or "persist(" in content:
            storage_files.append(file.relative_to(root).as_posix())
    scripts = []
    for file in sorted((root / "backend/scripts").glob("*.py")):
        tree = ast.parse(file.read_text(encoding="utf-8-sig"))
        scripts.append({"file": file.relative_to(root).as_posix(), "description": ast.get_docstring(tree) or ""})
    return {"repository": repo, "commit": commit, "workingTreeDirty": bool(dirty),
            "apiEndpoints": api, "uiRouteFile": route_file.relative_to(root).as_posix(),
            "uiRoutes": routes, "sourceFieldModels": field_models,
            "browserPersistenceFiles": storage_files, "scripts": scripts}


if __name__ == "__main__":
    repos = [inventory(repo) for repo in ("final-trade", "LaimiuTrade")]
    sys.path.insert(0, str(ROOT / "final-trade/backend"))
    # This registry is pure metadata construction; do not import app.main or its stores.
    from app.core.strategy_registry import StrategyRegistry

    output = {"schemaVersion": 1, "baselineDate": "2026-09-25",
              "status": "source-baseline-not-new-implementation",
              "scopeNote": "AST route inventory plus active strategy descriptors. Frontend-only behavior and scripts require the manual feature specification; endpoint counts are not feature completeness proof.",
              "repositories": repos,
              "strategies": [asdict(item) for item in StrategyRegistry().list()]}
    (DOCS / "source-inventory.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"apiCounts": {r["repository"]: len(r["apiEndpoints"]) for r in repos},
                      "routeCounts": {r["repository"]: len(r["uiRoutes"]) for r in repos},
                      "strategies": len(output["strategies"])}, ensure_ascii=True))
