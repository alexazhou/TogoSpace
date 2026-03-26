from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from util import configUtil

MIGRATIONS_TABLE = "_migrations"
MIGRATION_NAME_PATTERN = re.compile(r"^\d{4}.*\.sql$")
DEFAULT_MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent / "resource" / "migrate"
)


@dataclass(frozen=True)
class Migration:
    name: str
    applied_at: str | None = None


def resolve_db_path(db_path: str) -> Path:
    path = Path(db_path)
    if path.is_absolute():
        return path
    return (Path(__file__).resolve().parent / path).resolve()


def resolve_migrations_dir(migrations_dir: str | None) -> Path:
    if migrations_dir is None:
        return DEFAULT_MIGRATIONS_DIR
    path = Path(migrations_dir)
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def load_db_path_from_config(config_dir: str | None) -> str:
    setting = configUtil.load(config_dir).setting
    db_path = setting.persistence.db_path
    if not db_path.strip():
        raise ValueError("Invalid db_path in persistence config")
    return db_path


def connect_sqlite(db_path: Path) -> sqlite3.Connection:
    os.makedirs(db_path.parent, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def list_migration_files(migrations_dir: Path) -> list[Path]:
    if not migrations_dir.is_dir():
        raise FileNotFoundError(f"Migration directory not found: {migrations_dir}")
    return sorted(
        p for p in migrations_dir.iterdir()
        if p.is_file() and MIGRATION_NAME_PATTERN.match(p.name)
    )


def fetch_applied_migrations(conn: sqlite3.Connection) -> list[Migration]:
    rows = conn.execute(
        f"SELECT name, applied_at FROM {MIGRATIONS_TABLE} ORDER BY name"
    ).fetchall()
    return [Migration(name=row["name"], applied_at=row["applied_at"]) for row in rows]


def _is_ignorable_migration_error(exc: sqlite3.OperationalError) -> bool:
    msg = str(exc).lower()
    return "duplicate column name" in msg or "already exists" in msg


def apply_migration(
    conn: sqlite3.Connection,
    migration_file: Path,
    *,
    verbose: bool = False,
) -> None:
    sql = migration_file.read_text(encoding="utf-8")
    try:
        with conn:
            if sql.strip():
                conn.executescript(sql)
            conn.execute(
                f"INSERT INTO {MIGRATIONS_TABLE} (name) VALUES (?)",
                (migration_file.name,),
            )
    except sqlite3.OperationalError as exc:
        if not _is_ignorable_migration_error(exc):
            raise
        if verbose:
            print(
                "Ignore migration error and mark as applied: "
                f"{migration_file.name}: {exc}"
            )
        with conn:
            conn.execute(
                f"INSERT OR IGNORE INTO {MIGRATIONS_TABLE} (name) VALUES (?)",
                (migration_file.name,),
            )


def migrate_database(
    db_path: str | os.PathLike[str],
    *,
    migrations_dir: str | os.PathLike[str] | None = None,
    verbose: bool = False,
) -> list[str]:
    resolved_db_path = resolve_db_path(str(db_path))
    resolved_migrations_dir = resolve_migrations_dir(
        str(migrations_dir) if migrations_dir is not None else None
    )

    with connect_sqlite(resolved_db_path) as conn:
        ensure_migrations_table(conn)
        applied_names = {m.name for m in fetch_applied_migrations(conn)}
        migration_files = list_migration_files(resolved_migrations_dir)

        pending = [p for p in migration_files if p.name not in applied_names]
        applied_now: list[str] = []
        for migration_file in pending:
            if verbose:
                print(f"Applying migration: {migration_file.name}")
            apply_migration(conn, migration_file, verbose=verbose)
            applied_now.append(migration_file.name)
    return applied_now


def migration_status(
    db_path: str | os.PathLike[str],
    *,
    migrations_dir: str | os.PathLike[str] | None = None,
) -> tuple[list[Migration], list[str]]:
    resolved_db_path = resolve_db_path(str(db_path))
    resolved_migrations_dir = resolve_migrations_dir(
        str(migrations_dir) if migrations_dir is not None else None
    )

    with connect_sqlite(resolved_db_path) as conn:
        ensure_migrations_table(conn)
        applied = fetch_applied_migrations(conn)
        files = [p.name for p in list_migration_files(resolved_migrations_dir)]
    return applied, files


def clear_database(db_path: str | os.PathLike[str]) -> list[str]:
    resolved_db_path = resolve_db_path(str(db_path))
    dropped_tables: list[str] = []

    with connect_sqlite(resolved_db_path) as conn:
        table_rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()

        for row in table_rows:
            table_name = str(row["name"])
            quoted_name = table_name.replace('"', '""')
            conn.execute(f'DROP TABLE IF EXISTS "{quoted_name}"')
            dropped_tables.append(table_name)
        conn.commit()

    return dropped_tables


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SQLite migration management tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db-path", default=None, help="SQLite db path")
    common.add_argument("--config-dir", default=None, help="config directory path")
    common.add_argument(
        "--migrations-dir",
        default=None,
        help="migration directory path (default: resource/migrate)",
    )

    subparsers.add_parser("migrate", parents=[common], help="apply pending migrations")
    subparsers.add_parser("status", parents=[common], help="show migration status")
    subparsers.add_parser("init", parents=[common], help="alias of migrate")

    clear_parser = subparsers.add_parser(
        "clear",
        parents=[common],
        help="drop all custom tables in sqlite database",
    )
    clear_parser.add_argument(
        "--yes",
        action="store_true",
        help="skip confirmation before clearing database",
    )

    return parser


def _resolve_cli_db_path(args: argparse.Namespace) -> str:
    if args.db_path:
        return str(args.db_path)
    return load_db_path_from_config(args.config_dir)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    db_path = _resolve_cli_db_path(args)

    if args.command == "migrate":
        applied_now = migrate_database(
            db_path,
            migrations_dir=args.migrations_dir,
            verbose=True,
        )
        if applied_now:
            print(f"Applied {len(applied_now)} migration(s).")
        else:
            print("Database is up to date.")
        return 0

    if args.command == "status":
        applied_migrations, files = migration_status(
            db_path,
            migrations_dir=args.migrations_dir,
        )
        applied_map = {item.name: item.applied_at for item in applied_migrations}

        print("=== Migration Status ===")
        if not files:
            print("No migration files found.")
        for name in files:
            if name in applied_map:
                print(f"✅ {name} (Applied at: {applied_map[name]})")
            else:
                print(f"[ ] {name} (Pending)")
        print(f"Applied: {len(applied_migrations)}")
        print(f"Available: {len(files)}")
        print(f"Pending: {len(files) - len(applied_migrations)}")
        return 0


    if args.command == "clear":
        if not args.yes:
            answer = input(
                "This will drop all custom tables in the database. Continue? (y/N): "
            )
            if answer.strip().lower() != "y":
                print("Aborted.")
                return 0
        dropped = clear_database(db_path)
        if dropped:
            print(f"Dropped tables: {', '.join(dropped)}")
        else:
            print("No custom tables found to drop.")
        return 0

    print(f"Unknown command: {args.command}")
    return 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "Migration",
    "clear_database",
    "main",
    "migrate_database",
    "migration_status",
]
