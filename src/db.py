from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import appPaths
from util import configUtil

logger = logging.getLogger(__name__)

MIGRATIONS_TABLE = "_migrations"
MIGRATION_NAME_PATTERN = re.compile(r"^\d{4}.*\.(sql|py)$")


@dataclass(frozen=True)
class Migration:
    name: str
    applied_at: str | None = None


class DatabaseAheadOfCodeError(RuntimeError):
    """数据库比当前代码新：账本里存在本代码没有的迁移（#270）。

    为什么必须 fail fast：#270 的真实事故是 `0022` 已入库、但 5 小时里跑的是旧代码，
    那段时间写入的任务缺少节点链与 `current_owner_id`，界面用 fallback 合成假节点链，
    把「数据不存在」掩盖成「看起来有」。所以「库比代码新」时宁可拒绝启动，
    也不能让旧代码继续往一个新结构的库里写数据。

    **迁移纪律（#274 N-2）**：判据按**文件名**比对，所以已执行过的迁移文件名不可变更。
    迁移只许新增（`0023_…`），不许改名、不许改内容——改名会让账本里那个旧名字永久
    被判成「代码缺失」，本错误里 ② 分支就是提示这件事。

    属性：`missing` 列出「库中已执行、当前代码缺失」的迁移名（已排序），
    `code_latest` / `db_latest` 分别给出代码侧与库侧的最新迁移，便于一眼定位差距，
    `code_names` 是代码侧迁移名清单（用于区分「真·库超前」与「迁移被改名」）。
    """

    def __init__(
        self,
        missing: list[str],
        *,
        code_latest: str | None,
        db_latest: str | None,
        db_path: str | Path = "",
        code_names: list[str] | None = None,
        unparsed_names: list[str] | None = None,
    ) -> None:
        self.missing = sorted(missing)
        self.code_latest = code_latest
        self.db_latest = db_latest
        self.db_path = str(db_path)
        self.code_names = list(code_names or [])
        # 目录里存在、但不符合迁移命名规则的文件（如改名成 .txt / .bak / README.md）
        self.unparsed_names = list(unparsed_names or [])
        super().__init__(self._build_message())

    def _suspect_renames(self) -> list[str]:
        """同版本号前缀的代码侧文件名：最能区分「真·库超前」与「迁移被改名」。

        同时看两类：**被解析成迁移的**（改名成 `0022_…_v2.sql` 这种仍合规的名字）
        与**未被解析的**（改名成 `0022_….txt`/`.bak`/`.sql.bak` 后不再算迁移）。
        只看前者会漏掉更常见的后者。
        """
        prefixes = {name[:4] for name in self.missing}
        candidates = list(self.code_names) + list(self.unparsed_names)
        return [name for name in candidates if name[:4] in prefixes]

    def _build_message(self) -> str:
        missing = "、".join(self.missing)
        code_latest = self.code_latest or "无"
        db_latest = self.db_latest or "无"
        lines = [
            f"数据库比当前代码新，拒绝启动：库中已执行但当前代码缺失的迁移 = [{missing}]；"
            f"当前代码最新迁移 = {code_latest}，数据库最新迁移 = {db_latest}"
            f"{f'（{self.db_path}）' if self.db_path else ''}。",
            "两种原因，请先分清再动手：",
            "  ① 这个库确实被**更新的代码**迁移过：用当前（较旧）的代码连它会写出不完整的数据"
            " ⇒ 请升级到包含这些迁移的代码后再启动。",
            "  ② 迁移文件被**改名/删除**：判据按文件名比对，已执行过的迁移文件名不可变更"
            "（迁移只许新增、不许改名，内容也不要改）⇒ 请把文件名改回账本里记录的那个名字。",
            "排查线索：",
        ]
        if self.code_names:
            lines.append(
                f"  - 代码侧迁移共 {len(self.code_names)} 条（供比对）：{'、'.join(self.code_names)}"
            )
        else:
            lines.append("  - 代码侧迁移目录里一条迁移都没有（检查 --migrations-dir / assets/migrate 是否缺失）")
        suspects = self._suspect_renames()
        if suspects:
            lines.append(
                f"  - ⚠️ 与缺失项同版本号前缀的代码侧文件：{'、'.join(suspects)}"
                " ⇒ 高度疑似「迁移被改名」，请改回账本里的原名"
            )
        elif self.unparsed_names:
            lines.append(
                f"  - 目录里有 {len(self.unparsed_names)} 个不符合迁移命名规则的文件："
                f"{'、'.join(self.unparsed_names)}（不是迁移，不参与判据）"
            )
            lines.append("  - 代码侧没有与缺失项同版本号的迁移 ⇒ 更像 ①（库被更新代码迁移过）")
        else:
            lines.append("  - 代码侧没有与缺失项同版本号的迁移 ⇒ 更像 ①（库被更新代码迁移过）")
        lines.extend([
            "请任选一种方式自救：",
            "  1) 升级到包含这些迁移的代码后再启动（git pull / 切到最新主干）；",
            "  2) 若你在较旧的 worktree 上开发，改用独立数据目录（设置 STORAGE_ROOT 指向自己的目录），"
            "不要与共享库共用；",
            "  3) 确认这些迁移确实该丢弃时，走人工评审后重建数据库。",
            "禁止：删除 `_migrations` 账本记录、手工回退表结构、或用旧版本直接读写该库——"
            "那会让数据结构与代码再次不一致。",
        ])
        return "\n".join(lines)


def resolve_db_path(db_path: str) -> Path:
    path = Path(db_path)
    if path.is_absolute():
        return path
    return (Path(__file__).resolve().parent / path).resolve()


def resolve_migrations_dir(migrations_dir: str | None) -> Path:
    if migrations_dir is None:
        return Path(appPaths.ASSETS_DIR) / "migrate"
    path = Path(migrations_dir)
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def load_db_path_from_config(config_dir: str | None) -> str:
    setting = configUtil.load(config_dir).setting
    db_path = setting.db_path
    if not db_path.strip():
        raise ValueError("Invalid db_path in config")
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


def list_unparsed_files(migrations_dir: Path, migration_files: list[Path]) -> list[str]:
    """目录里存在、但**不符合迁移命名规则**的文件名（#274 N-2）。

    为什么需要：迁移被改名成 `.txt`/`.bak` 之后就不算迁移了，单看「已解析的迁移名」
    会以为代码里根本不存在这个版本号的文件，排查时白白去找「哪段代码新增了这条迁移」。
    """
    if not migrations_dir.is_dir():
        return []
    parsed = {p.name for p in migration_files}
    return sorted(
        p.name for p in migrations_dir.iterdir() if p.is_file() and p.name not in parsed
    )


def fetch_applied_migrations(conn: sqlite3.Connection) -> list[Migration]:
    rows = conn.execute(
        f"SELECT name, applied_at FROM {MIGRATIONS_TABLE} ORDER BY name"
    ).fetchall()
    return [Migration(name=row["name"], applied_at=row["applied_at"]) for row in rows]


def _is_ignorable_migration_error(exc: sqlite3.OperationalError) -> bool:
    msg = str(exc).lower()
    return "duplicate column name" in msg or "already exists" in msg


def missing_migrations_in_code(
    applied_names: set[str] | list[str],
    code_names: list[str],
) -> list[str]:
    """返回「库中已执行、当前代码缺失」的迁移名（#270）。

    反向差集：`applied - code`。反过来（`code - applied`）是待补执行的迁移，
    那是既有行为（自动补执行），不在本函数语义内。
    """
    code_set = set(code_names)
    return sorted(name for name in applied_names if name not in code_set)


def _ensure_db_not_ahead_of_code(
    applied_names: set[str],
    code_names: list[str],
    db_path: Path,
    unparsed_names: list[str] | None = None,
) -> None:
    """库比代码新 ⇒ 直接拒绝，不写库、不跳过（#270）。

    放在「应用待执行迁移」之前：拒绝启动时库必须保持原样，一个字节都不动。
    """
    missing = missing_migrations_in_code(applied_names, code_names)
    if not missing:
        return
    logger.error(
        "数据库比当前代码新，拒绝启动：缺失迁移 = %s（代码最新=%s，库最新=%s）",
        missing,
        code_names[-1] if code_names else "无",
        max(applied_names) if applied_names else "无",
    )
    raise DatabaseAheadOfCodeError(
        missing,
        code_latest=code_names[-1] if code_names else None,
        db_latest=max(applied_names) if applied_names else None,
        db_path=db_path,
        code_names=code_names,
        unparsed_names=unparsed_names,
    )


def _rollback_if_in_transaction(conn: sqlite3.Connection) -> None:
    """把连接从「事务未结束」状态拉回干净状态（#253 M-1）。

    为什么需要：`executescript()` 会**先隐式 COMMIT 当前事务**、再逐条执行脚本内容，
    完全绕过 `with conn:` 的事务管理。若迁移脚本自带 `BEGIN; … COMMIT;` 且中途报错，
    连接会停在「事务已开、尚未提交」的状态；此时若再走进 `with conn:`（例如把该
    迁移记为已应用），就会把**半个脚本**提交掉。故在异常分支的最前面先兜底回滚。
    """
    if conn.in_transaction:
        conn.rollback()


def _run_python_migration(
    migration_file: Path,
    workspace_root: str,
) -> None:
    """执行 Python 迁移脚本。

    脚本必须提供 migrate(workspace_root) 函数，且不访问数据库（迁移跑在 ORM 绑定之前）。
    文件系统操作不在 SQL 事务内，脚本需自行保证幂等；失败时整次迁移中止、不记账。
    """
    module_name = f"_togospace_migration_{migration_file.stem}"
    spec = importlib.util.spec_from_file_location(module_name, str(migration_file))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载迁移脚本: {migration_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    migrate_func = getattr(module, "migrate", None)
    if not callable(migrate_func):
        raise AttributeError(f"迁移脚本缺少 migrate(workspace_root) 函数: {migration_file}")

    migrate_func(workspace_root)


def apply_migration(
    conn: sqlite3.Connection,
    migration_file: Path,
    *,
    workspace_root: str = "",
    verbose: bool = False,
) -> None:
    # Python 迁移只做文件系统操作、不接触数据库；执行成功后才写入账本，
    # 中途失败不会记账，下次启动会重跑（脚本需自行保证幂等）
    if migration_file.suffix == ".py":
        _run_python_migration(migration_file, workspace_root)
        with conn:
            conn.execute(
                f"INSERT INTO {MIGRATIONS_TABLE} (name) VALUES (?)",
                (migration_file.name,),
            )
        return

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
        # 先兜底回滚：否则下面「记账」的 `with conn:` 会把脚本已执行的部分提交掉
        _rollback_if_in_transaction(conn)
        if not _is_ignorable_migration_error(exc):
            raise
        if verbose:
            logger.info(
                "Ignore migration error and mark as applied: "
                f"{migration_file.name}: {exc}"
            )
        with conn:
            conn.execute(
                f"INSERT OR IGNORE INTO {MIGRATIONS_TABLE} (name) VALUES (?)",
                (migration_file.name,),
            )
    except BaseException:
        # 非 OperationalError（如 IntegrityError / 中断）同样要保证连接不带事务残留
        _rollback_if_in_transaction(conn)
        raise


def migrate_database(
    db_path: str | os.PathLike[str],
    *,
    migrations_dir: str | os.PathLike[str] | None = None,
    up_to: str | None = None,
    verbose: bool = False,
    workspace_root: str = "",
) -> list[str]:
    """按文件名顺序执行待迁移文件：.sql 直接执行，.py 调用脚本内的 migrate(workspace_root)。"""
    resolved_db_path = resolve_db_path(str(db_path))
    resolved_migrations_dir = resolve_migrations_dir(
        str(migrations_dir) if migrations_dir is not None else None
    )

    with connect_sqlite(resolved_db_path) as conn:
        ensure_migrations_table(conn)
        applied_names = {m.name for m in fetch_applied_migrations(conn)}
        migration_files = list_migration_files(resolved_migrations_dir)

        # ── 闸门（#270）：库比代码新 ⇒ 拒绝启动，且在应用任何迁移之前判 ──────────
        # 反向差集非空意味着这个库被更新的代码迁过；旧代码继续跑会把数据写成
        # 「结构新、写入旧」的半残状态（#270 的真实事故），所以这里 fail fast。
        _ensure_db_not_ahead_of_code(
            applied_names,
            [p.name for p in migration_files],
            resolved_db_path,
            list_unparsed_files(resolved_migrations_dir, migration_files),
        )

        pending = [p for p in migration_files if p.name not in applied_names]
        if up_to is not None:
            threshold = int(up_to)
            pending = [p for p in pending if int(p.name[:4]) < threshold]
        applied_now: list[str] = []
        if verbose:
            applied_sorted = sorted(applied_names)
            current_version = applied_sorted[-1] if applied_sorted else "无"
            logger.info(
                f"数据库迁移检查: 已执行={len(applied_names)}/{len(migration_files)}, 当前版本={current_version}, 待执行={len(pending)}"
            )
        for migration_file in pending:
            if verbose:
                logger.info(f"Applying migration: {migration_file.name}")
            apply_migration(
                conn,
                migration_file,
                workspace_root=workspace_root,
                verbose=verbose,
            )
            applied_now.append(migration_file.name)
        if verbose and applied_now:
            logger.info(f"数据库迁移完成: 本次执行={len(applied_now)}, 当前版本={applied_now[-1]}")
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


def has_pending_migrations(
    db_path: str | os.PathLike[str],
    *,
    migrations_dir: str | os.PathLike[str] | None = None,
) -> bool:
    """判断数据库是否落后于迁移目录（存在尚未记账的迁移文件）。"""
    applied, files = migration_status(db_path, migrations_dir=migrations_dir)
    applied_names = {migration.name for migration in applied}
    return any(name not in applied_names for name in files)


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


def check_database_initialized(db_path: str | os.PathLike[str]) -> bool:
    """检查数据库是否已初始化（_migrations 表存在）。"""
    resolved_db_path = resolve_db_path(str(db_path))
    if not resolved_db_path.exists():
        return False
    with sqlite3.connect(str(resolved_db_path)) as conn:
        row = conn.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name='{MIGRATIONS_TABLE}'"
        ).fetchone()
    return row is not None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SQLite migration management tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db-path", default=None, help="SQLite db path")
    common.add_argument("--config-dir", default=None, help="config directory path")
    common.add_argument(
        "--migrations-dir",
        default=None,
        help="migration directory path (default: assets/migrate)",
    )

    migrate_parser = subparsers.add_parser("migrate", parents=[common], help="apply pending migrations")
    migrate_parser.add_argument(
        "--up-to",
        default=None,
        metavar="PREFIX",
        help="only apply migrations whose filename is less than PREFIX (e.g. 0011)",
    )
    migrate_parser.add_argument(
        "--workspace-root",
        default=None,
        help="workspace root passed to python migrations (default: setting.json / appPaths.WORKSPACE_ROOT)",
    )
    subparsers.add_parser("status", parents=[common], help="show migration status")
    subparsers.add_parser("init", parents=[common], help="alias of migrate")
    subparsers.add_parser("check", parents=[common], help="check if database is initialized")

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


def _resolve_cli_workspace_root(args: argparse.Namespace) -> str:
    """workspace_root 来自 setting.json（不是数据库），与 db_path 的解析来源保持一致。"""
    if args.workspace_root:
        return str(args.workspace_root)
    if args.config_dir is not None or args.db_path is None:
        return str(configUtil.load(args.config_dir).setting.workspace_root)
    return appPaths.WORKSPACE_ROOT


def main(argv: list[str] | None = None) -> int:
    # CLI 独立进程：后端日志系统未初始化，这里补一个最简配置，保证迁移进度可见
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    args = _build_parser().parse_args(argv)
    db_path = _resolve_cli_db_path(args)

    if args.command == "migrate":
        try:
            applied_now = migrate_database(
                db_path,
                migrations_dir=args.migrations_dir,
                up_to=args.up_to,
                verbose=True,
                workspace_root=_resolve_cli_workspace_root(args),
            )
        except DatabaseAheadOfCodeError as exc:
            # 与后端启动路径统一（#274 N-4）：exit=2 + 可读错误，不打裸 traceback。
            # 两条路径同一个语义（库比代码新 ⇒ 拒绝），退出码也统一为 2；
            # `check` 的 1 表示「库未初始化」，是不同的语义，保持不变。
            print(f"[TOGO] ERROR {exc}", file=sys.stderr)
            return 2
        if applied_now:
            print(f"Applied {len(applied_now)} migration(s).")
        else:
            print("Database is up to date.")
        return 0

    if args.command == "check":
        if check_database_initialized(db_path):
            print("Database is initialized.")
            return 0
        else:
            print("Database is NOT initialized. Run 'migrate' first.")
            return 1

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
    "DatabaseAheadOfCodeError",
    "Migration",
    "check_database_initialized",
    "clear_database",
    "has_pending_migrations",
    "list_unparsed_files",
    "main",
    "migrate_database",
    "migration_status",
    "missing_migrations_in_code",
]
