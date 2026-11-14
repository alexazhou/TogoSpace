"""MCP 注册表 service 层判据（#224，按 #223 设计稿）。

三类判据分开，避免「跑绿了但没测到东西」：
1. **持久化**：真迁移建库（`container_db_schema.migrate_all`，含 0022），save →
   **重开连接**（模拟重启）→ list 仍可见 —— M1 变异的靶子；
2. **校验正反**：别名格式 / kind / target / 凭证引用（明文拒、file:/keychain: 未实现
   不降级、env: 通过、结构错、kind 错配）；
3. **并发与覆盖**：唯一索引兜底（撞索引转 mcp_alias_exists）、overwrite 显式覆盖、
   rm 不存在报 mcp_alias_not_found（不静默成功）。

判据有效性：本文件用例在 #224 代码合入前跑不起来（表 / service 都不存在）。
"""

from __future__ import annotations

from pathlib import Path

import peewee
import pytest

from container_db_schema import migrate_all
from dal.db import gtMcpServerManager
from exception import TogoException
from model.dbModel.base import bind_database, unbind_database
from model.dbModel.gtMcpServer import (
    GtMcpServer,
    mask_credentials_for_display,
    mask_target,
)
from service import mcpRegistryService as svc
from service.ormService import AioSqliteDatabase


@pytest.fixture
async def mcp_db(tmp_path: Path):
    """把模型代理临时绑到「已跑完全部迁移」的临时 SQLite 文件上，结束后一定解绑。"""
    db_path = tmp_path / "mcp_registry.db"
    applied = migrate_all(db_path, workspace_root=tmp_path)
    assert "0022_add_mcp_servers.sql" in applied, f"0022 未被执行：{applied}"

    db = AioSqliteDatabase(str(db_path), pool_params={"timeout": 30})
    bind_database(db)
    await db.aio_connect()
    try:
        yield db_path
    finally:
        await db.aio_close()
        unbind_database()


@pytest.fixture
async def reopened_db(mcp_db: Path):
    """M1 判据的「重启」语义：对同一个库文件**再开一个连接池**并换绑代理。

    旧连接池由 `mcp_db` 的 teardown 统一关闭（依赖 teardown 顺序在此之后）；
    两个连接池指向同一文件读同一份数据，正是「重启后再看」的真实语义。
    """
    db = AioSqliteDatabase(str(mcp_db), pool_params={"timeout": 30})
    bind_database(db)
    await db.aio_connect()
    try:
        yield db
    finally:
        await db.aio_close()
        # 换绑回旧库对象没必要（mcp_db 的 teardown 会解绑 proxy），这里只关新池。


async def _save(alias: str = "ctx7", **overrides) -> GtMcpServer:
    kwargs = dict(
        team_id=1,
        alias=alias,
        kind="mcp",
        target="https://mcp.context7.com/mcp",
        note="文档检索",
        created_by=7,
    )
    kwargs.update(overrides)
    return await svc.save_server(**kwargs)


# ---------------------------------------------------------------------------
# 持久化（M1 靶子）
# ---------------------------------------------------------------------------


async def test_save_persists_across_reconnect(mcp_db, reopened_db):
    """save 真写库：重开连接池（≈重启）后 list 仍看到该条目。

    M1 变异（save 不落库 / 落内存）会让本条变红——重启后什么都没有。
    """
    entry = await _save()
    assert entry.id is not None

    rows = [r.alias for r in await svc.list_servers(1)]
    assert rows == ["ctx7"], f"重启后注册表不应为空：{rows}"

    fresh = await gtMcpServerManager.get_by_alias(1, "ctx7")
    assert fresh is not None
    assert fresh.kind == "mcp"
    assert fresh.credentials == {}  # E1：空凭证统一存 {}


async def test_save_writes_created_by_for_audit(mcp_db):
    entry = await _save(created_by=42)
    fresh = await gtMcpServerManager.get_by_alias(1, "ctx7")
    assert fresh.created_by == 42


# ---------------------------------------------------------------------------
# 别名 / kind / target 校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alias", ["ctx7", "github-mcp", "a", "x_1"])
async def test_alias_formats_accepted(mcp_db, alias):
    entry = await _save(alias=alias)
    assert entry.alias == alias


@pytest.mark.parametrize(
    "alias",
    ["", "  ", "Ctx7", "1abc", "-abc", "a b", "a" * 33, "-h"],
)
async def test_alias_formats_refused(mcp_db, alias):
    with pytest.raises(TogoException) as excinfo:
        await _save(alias=alias)
    assert excinfo.value.error_code == "mcp_invalid_alias"


@pytest.mark.parametrize("kind", ["websocket", "MCP", ""])
async def test_kind_refused(mcp_db, kind):
    with pytest.raises(TogoException) as excinfo:
        await _save(kind=kind)
    assert excinfo.value.error_code == "mcp_invalid_kind"


@pytest.mark.parametrize("target", ["", "   ", None])
async def test_target_refused_when_blank(mcp_db, target):
    with pytest.raises(TogoException) as excinfo:
        await _save(target=target)
    assert excinfo.value.error_code == "mcp_invalid_target"


# ---------------------------------------------------------------------------
# 凭证纪律（1337 附加判据 2：file:/keychain: 未实现不降级，正反双向）
# ---------------------------------------------------------------------------


async def test_empty_credentials_normalized_to_empty_dict(mcp_db):
    for index, given in enumerate((None, {}, {"headers": {}}, {"env": {}})):
        entry = await _save(alias=f"zero-cred-{index}", credentials=given)
        assert entry.credentials == {}, given


async def test_env_ref_is_accepted_and_stored_verbatim(mcp_db):
    """正例：env: 引用原样入库（一个 key 一个引用，B6）。"""
    entry = await _save(
        alias="gh",
        credentials={"headers": {"Authorization": "env:GITHUB_TOKEN"}},
    )
    assert entry.credentials == {"headers": {"Authorization": "env:GITHUB_TOKEN"}}


@pytest.mark.parametrize(
    "ref",
    ["ghp_SUPER_SECRET", "Bearer abc"],
)
async def test_inline_plaintext_refused(mcp_db, ref):
    """反例①：明文一律拒（mcp_inline_credential_refused），提示改用 env: 引用。"""
    with pytest.raises(TogoException) as excinfo:
        await _save(
            alias="leak",
            credentials={"headers": {"Authorization": ref}},
        )
    assert excinfo.value.error_code == "mcp_inline_credential_refused"


async def test_empty_ref_value_refused(mcp_db):
    with pytest.raises(TogoException) as excinfo:
        await _save(alias="empty", credentials={"headers": {"Authorization": "  "}})
    assert excinfo.value.error_code == "mcp_invalid_credentials"


@pytest.mark.parametrize("ref", ["file:/etc/secrets/token", "keychain:mcp/gh"])
async def test_unimplemented_refs_refused_not_downgraded(mcp_db, ref):
    """反例②：file:/keychain: 报「未实现」，**不许退化成当明文用**。"""
    with pytest.raises(TogoException) as excinfo:
        await _save(alias="unimpl", credentials={"headers": {"Authorization": ref}})
    assert excinfo.value.error_code == "mcp_credential_ref_unimplemented"


@pytest.mark.parametrize("ref", ["s3:bucket", "vault:kv/x"])
async def test_unknown_ref_prefix_refused(mcp_db, ref):
    with pytest.raises(TogoException) as excinfo:
        await _save(alias="unknown", credentials={"headers": {"Authorization": ref}})
    assert excinfo.value.error_code == "mcp_inline_credential_refused"


async def test_credentials_structure_violations(mcp_db):
    with pytest.raises(TogoException) as unknown_layer:
        await _save(alias="s1", credentials={"cookies": {"sid": "env:S"}})
    assert unknown_layer.value.error_code == "mcp_invalid_credentials"

    with pytest.raises(TogoException) as bad_layer:
        await _save(alias="s2", credentials={"headers": ["env:A"]})
    assert bad_layer.value.error_code == "mcp_invalid_credentials"


async def test_credential_kind_mismatch_refused(mcp_db):
    """headers 配 stdio / env 配 HTTP 都在登记时拒——静默存下调用时永不生效的凭证更坏。"""
    with pytest.raises(TogoException) as header_stdio:
        await _save(
            alias="s3", kind="mcp_stdio", target="npx -y some-server",
            credentials={"headers": {"Authorization": "env:T"}},
        )
    assert header_stdio.value.error_code == "mcp_credential_kind_mismatch"

    with pytest.raises(TogoException) as env_http:
        await _save(
            alias="s4", kind="mcp", target="https://x",
            credentials={"env": {"API_KEY": "env:API_KEY"}},
        )
    assert env_http.value.error_code == "mcp_credential_kind_mismatch"

    # 对称正向：env 凭证配 mcp_stdio 合法。
    entry = await _save(
        alias="s5", kind="mcp_stdio", target="npx -y some-server",
        credentials={"env": {"API_KEY": "env:API_KEY"}},
    )
    assert entry.credentials == {"env": {"API_KEY": "env:API_KEY"}}


# ---------------------------------------------------------------------------
# 唯一索引 / overwrite / rm
# ---------------------------------------------------------------------------


async def test_second_add_without_overwrite_refused(mcp_db):
    await _save(target="https://mcp.context7.com/mcp?api_key=TOPSECRET")
    with pytest.raises(TogoException) as excinfo:
        await _save(kind="openapi", target="https://other.example.com")
    assert excinfo.value.error_code == "mcp_alias_exists"
    message = str(excinfo.value)
    # 现有条目摘要按 §4.5 打码：query/长 token 不回显，可读段（域名）允许。
    assert "TOPSECRET" not in message, "报错里不得出现 target 内嵌凭证值"
    assert "https://mcp.context7.com/mcp?***" in message


async def test_unique_index_is_the_real_backstop(mcp_db):
    """并发第二写直接撞唯一索引（绕过 service 预检也拦得住）。"""
    row = GtMcpServer(team_id=1, alias="raw", kind="mcp", target="https://a")
    await row.aio_save(force_insert=True)
    duplicate = GtMcpServer(team_id=1, alias="raw", kind="mcp", target="https://b")
    with pytest.raises(peewee.IntegrityError):
        await duplicate.aio_save(force_insert=True)


async def test_overwrite_updates_existing_row(mcp_db):
    await _save()
    updated = await _save(
        kind="openapi",
        target="https://spec.example.com/openapi.json",
        note="换目标",
        credentials={"headers": {"Authorization": "env:T"}},
        overwrite=True,
    )
    assert updated.id == (await gtMcpServerManager.get_by_alias(1, "ctx7")).id
    rows = await svc.list_servers(1)
    assert len(rows) == 1, "覆盖不得产生第二条记录"
    fresh = rows[0]
    assert fresh.kind == "openapi"
    assert fresh.target == "https://spec.example.com/openapi.json"
    assert fresh.credentials == {"headers": {"Authorization": "env:T"}}


async def test_delete_removes_exactly_one_row(mcp_db):
    await _save()
    removed = await svc.delete_server(1, "ctx7")
    assert removed.alias == "ctx7"
    assert await gtMcpServerManager.get_by_alias(1, "ctx7") is None

    with pytest.raises(TogoException) as excinfo:
        await svc.delete_server(1, "ctx7")
    assert excinfo.value.error_code == "mcp_alias_not_found"


async def test_delete_is_team_scoped(mcp_db):
    await _save()  # team 1
    team2_row = GtMcpServer(team_id=2, alias="ctx7", kind="mcp", target="https://team2")
    await team2_row.aio_save(force_insert=True)
    await svc.delete_server(2, "ctx7")
    # team 1 的同名别名不受影响（team_id 隔离）。
    assert await gtMcpServerManager.get_by_alias(1, "ctx7") is not None


# ---------------------------------------------------------------------------
# resolve_alias / 摘要 / 脱敏
# ---------------------------------------------------------------------------


async def test_resolve_alias_hit_miss_and_blank(mcp_db):
    await _save()
    hit = await svc.resolve_alias(1, "  ctx7  ")
    assert hit is not None and hit.target == "https://mcp.context7.com/mcp"
    assert await svc.resolve_alias(1, "nope") is None
    assert await svc.resolve_alias(1, "") is None  # 空不是解析场景（设计稿 §3.6-1）
    assert await svc.resolve_alias(2, "ctx7") is None  # team 隔离


async def test_entry_summary_masks_target_and_flags_credentials(mcp_db):
    await _save(
        target="https://api.example.com/v1?api_key=should_never_leak",
        credentials={"headers": {"Authorization": "env:GITHUB_TOKEN"}},
    )
    entry = await gtMcpServerManager.get_by_alias(1, "ctx7")
    summary = svc.entry_summary(entry)
    assert summary["alias"] == "ctx7"
    assert "should_never_leak" not in json_dumps(summary)
    assert summary["has_credentials"] is True
    assert "target_masked" in summary and "target" not in summary


def json_dumps(value, **kwargs) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, **kwargs)


def test_mask_target_masks_query_and_long_tokens():
    assert mask_target("https://x.com/a?api_key=secret") == "https://x.com/a?***"
    assert (
        mask_target("https://x.com/p/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa?q=1")
        == "https://x.com/p/***?***"
    )
    assert mask_target("https://x.com/short/path") == "https://x.com/short/path"


def test_mask_credentials_keeps_refs_only():
    masked = mask_credentials_for_display(
        {"headers": {"Authorization": "env:GITHUB_TOKEN"}, "env": {}}
    )
    assert masked == {"headers": {"Authorization": "env:GITHUB_TOKEN"}}
    # 引用白名单之外的值只露前缀（file:***），不回显可定位值。
    masked_file = mask_credentials_for_display({"headers": {"X": "file:/etc/t"}})
    assert masked_file == {"headers": {"X": "file:***"}}


async def test_model_to_json_never_leaks(mcp_db):
    await _save(
        target="https://api.example.com/v1?api_key=should_never_leak",
        credentials={"headers": {"Authorization": "env:GITHUB_TOKEN"}},
    )
    entry = await gtMcpServerManager.get_by_alias(1, "ctx7")
    data = entry.to_json()
    dumped = json_dumps(data, default=str)
    assert "should_never_leak" not in dumped
    assert "env:GITHUB_TOKEN" in dumped  # 引用原文允许展示（§4.5-1）
    assert "target" not in data and data["target_masked"].endswith("?***")


async def test_credential_refs_of_returns_verbatim_refs(mcp_db):
    """1331 硬条件 ⅰ 的 service 侧：refs 原样取出，本层不解析、不持有值。"""
    await _save(
        credentials={"headers": {"Authorization": "env:GITHUB_TOKEN"}},
    )
    entry = await gtMcpServerManager.get_by_alias(1, "ctx7")
    refs = svc.credential_refs_of(entry)
    assert refs == {"headers": {"Authorization": "env:GITHUB_TOKEN"}, "env": {}}
