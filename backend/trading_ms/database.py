import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


def resolve_root_dir() -> Path:
    """exe 同目录 / 源码根目录。data_location.txt 与默认 data/ 都放这里。"""
    if getattr(sys, "frozen", False):
        # PyInstaller 打包运行：以 exe 所在目录为根
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


ROOT_DIR = resolve_root_dir()

# 数据目录可由 data_location.txt（一行绝对路径）覆盖；不存在则默认 ROOT_DIR/data
_LOCATION_FILE = ROOT_DIR / "data_location.txt"


def _read_location_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    line = path.read_text(encoding="utf-8").strip()
    if not line:
        return None
    p = Path(line).expanduser()
    if not p.is_absolute():
        p = (path.parent / p).resolve()
    return p


def _has_database(path: Path) -> bool:
    return (path / "laimiutrade.db").is_file()


def _find_existing_data_dir() -> Path | None:
    """打包后从 exe 目录向上查找已有数据，避免 dist/ 下新建空库。"""
    if not getattr(sys, "frozen", False):
        return None

    for loc_file in (_LOCATION_FILE, ROOT_DIR.parent / "data_location.txt"):
        target = _read_location_file(loc_file)
        if target and _has_database(target):
            return target

    for candidate in (ROOT_DIR.parent / "data",):
        if _has_database(candidate):
            return candidate.resolve()
    return None


def _pick_data_dir(default: Path, legacy: Path | None) -> Path:
    if legacy is None:
        return default
    if not _has_database(default):
        return legacy
    if not _has_database(legacy):
        return default
    default_db = default / "laimiutrade.db"
    legacy_db = legacy / "laimiutrade.db"
    if legacy_db.stat().st_size > default_db.stat().st_size:
        return legacy
    return default


def _resolve_data_dir() -> Path:
    target = _read_location_file(_LOCATION_FILE)
    if target:
        return target

    default = ROOT_DIR / "data"
    legacy = _find_existing_data_dir()
    chosen = _pick_data_dir(default, legacy)
    if legacy and chosen == legacy:
        try:
            _LOCATION_FILE.write_text(str(chosen), encoding="utf-8")
        except OSError:
            pass
    return chosen


DATA_DIR = _resolve_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "laimiutrade.db"

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def ensure_schema() -> None:
    """SQLite 轻量迁移：为已有库补列。"""
    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(daily_reviews)"))}
        if "trade_scores" not in cols:
            conn.execute(text("ALTER TABLE daily_reviews ADD COLUMN trade_scores TEXT DEFAULT '{}'"))

        snap_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(snapshots)"))}
        if "positions" not in snap_cols:
            conn.execute(text("ALTER TABLE snapshots ADD COLUMN positions TEXT DEFAULT '[]'"))
        if "available_cash" not in snap_cols:
            conn.execute(text("ALTER TABLE snapshots ADD COLUMN available_cash REAL"))
        if "position_value" not in snap_cols:
            conn.execute(text("ALTER TABLE snapshots ADD COLUMN position_value REAL"))

        review_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(daily_reviews)"))}
        if "next_position_rehearsal" not in review_cols:
            conn.execute(text("ALTER TABLE daily_reviews ADD COLUMN next_position_rehearsal TEXT DEFAULT '[]'"))
        if "rehearsal_ai_analysis" not in review_cols:
            conn.execute(text("ALTER TABLE daily_reviews ADD COLUMN rehearsal_ai_analysis TEXT DEFAULT ''"))

        weekly_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(weekly_reviews)"))}
        if "market_review" not in weekly_cols:
            conn.execute(text("ALTER TABLE weekly_reviews ADD COLUMN market_review TEXT DEFAULT ''"))


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
