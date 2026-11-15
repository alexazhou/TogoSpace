"""MCP 注册表 service 层判据（#273，全局注册表版）。

三类判据分开，避免「跑绿了但没测到东西」：
1. **持久化**：真迁移建库（`container_db_schema.migrate_all`，含 0023），save →
   **重开连接**（模拟重启）→ list 仍可见 —— M1 变异的靶子；
2. **校验正反**：name 格式 / kind / target / 凭证引用（明文拒、file:/keychain: 未实现
   不降级、env: 通过、结构错、kind 错配）；
3. **并发与覆盖**：唯一索引兜底（撞索引转 mcp_name_exists）、overwrite 显式覆盖、
   rm 不存在报 mcp_name_not_found（不静默成功）；
4. **enable 三态（#273）**：set_enabled 切换、禁用条目仍在 list 里、save 不动 enable。

判据有效性：本文件用例在 #273 代码合入前跑不起来（表结构 / service 形态都不存在）。
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
    mask_config_for_display,
    mask_target,
)
from service import mcpRegistryService as svc
from service.ormService import AioSqliteDatabase


@pytest.fixture
async def mcp_db(tmp_path: Path):
    """把模型代理临时绑到「已跑完全部迁移」的临时 SQLite 文件上，结束后一定解绑。"""
    db_path = tmp_path / "mcp_registry.db"
    applied = migrate_all(db_path, workspace_root=tmp_path)
    assert "0023_add_mcp_servers.sql" in applied, f"0023 未被执行：{applied}"
    # 0022（agent_task_stages）与 0023（mcp_servers）同库共存：0023 必须排在 0022 后。
    assert "0022_add_agent_task_stages.sql" in applied

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


async def _save(name: str = "ctx7", **overrides) -> GtMcpServer:
    kwargs = dict(
        name=name,
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

    rows = [r.name for r in await svc.list_servers()]
    assert rows == ["ctx7"], f"重启后注册表不应为空：{rows}"

    fresh = await gtMcpServerManager.get_by_name("ctx7")
    assert fresh is not None
    assert fresh.kind == "mcp"
    assert fresh.protocol == "streamable_http"  # kind=mcp 缺省传输
    assert fresh.config == {}  # E1：空凭证统一存 {}
    assert fresh.enable == 1  # 新登记默认启用


async def test_save_writes_created_by_for_audit(mcp_db):
    entry = await _save(created_by=42)
    fresh = await gtMcpServerManager.get_by_name("ctx7")
    assert fresh.created_by == 42


# ---------------------------------------------------------------------------
# name / kind / target 校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["ctx7", "github-mcp", "a", "x_1"])
async def test_name_formats_accepted(mcp_db, name):
    entry = await _save(name=name)
    assert entry.name == name


@pytest.mark.parametrize(
    "name",
    ["", "  ", "Ctx7", "1abc", "-abc", "a b", "a" * 33, "-h"],
)
async def test_name_formats_refused(mcp_db, name):
    with pytest.raises(TogoException) as excinfo:
        await _save(name=name)
    assert excinfo.value.error_code == "mcp_invalid_name"


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


async def test_empty_config_normalized_to_empty_dict(mcp_db):
    for index, given in enumerate((None, {}, {"headers": {}}, {"env": {}})):
        entry = await _save(name=f"zero-cred-{index}", config=given)
        assert entry.config == {}, given


async def test_values_stored_verbatim_any_string(mcp_db):
    """#277：值原样存——明文、任意字符串都不再被拒（旧「明文拒绝/file:/keychain: 未实现」校验已删）。"""
    cases = [
        "Bearer sk-TESTONLY-1234567890",
        "env:GITHUB_TOKEN",       # 旧引用语法也只是普通字符串，原样存
        "file:/etc/secrets/token",
        "s3:bucket",
        "plain-password",
    ]
    for index, value in enumerate(cases):
        entry = await _save(
            name=f"verbatim-{index}",
            config={"headers": {"Authorization": value}},
        )
        assert entry.config == {"headers": {"Authorization": value}}, value


async def test_value_must_be_nonempty_string(mcp_db):
    """结构校验保留：空串 / 非字符串仍拒（mcp_invalid_credentials）。"""
    with pytest.raises(TogoException) as empty:
        await _save(name="empty-str", config={"headers": {"Authorization": "  "}})
    assert empty.value.error_code == "mcp_invalid_credentials"

    with pytest.raises(TogoException) as non_str:
        await _save(name="num-str", config={"headers": {"Authorization": 123}})
    assert non_str.value.error_code == "mcp_invalid_credentials"


async def test_config_structure_violations(mcp_db):
    with pytest.raises(TogoException) as unknown_layer:
        await _save(name="s1", config={"cookies": {"sid": "env:S"}})
    assert unknown_layer.value.error_code == "mcp_invalid_credentials"

    with pytest.raises(TogoException) as bad_layer:
        await _save(name="s2", config={"headers": ["env:A"]})
    assert bad_layer.value.error_code == "mcp_invalid_credentials"


async def test_credential_protocol_mismatch_refused(mcp_db):
    """headers 配 stdio / env 配 HTTP 传输都在登记时拒——静默存下调用时永不生效的凭证更坏。"""
    with pytest.raises(TogoException) as header_stdio:
        await _save(
            name="s3", kind="mcp", protocol="stdio", target="npx -y some-server",
            config={"headers": {"Authorization": "env:T"}},
        )
    assert header_stdio.value.error_code == "mcp_credential_kind_mismatch"

    with pytest.raises(TogoException) as env_http:
        await _save(
            name="s4", kind="mcp", target="https://x",
            config={"env": {"API_KEY": "env:API_KEY"}},
        )
    assert env_http.value.error_code == "mcp_credential_kind_mismatch"

    # 对称正向：env 层配 kind=mcp + protocol=stdio 合法（值原样存）。
    entry = await _save(
        name="s5", kind="mcp", protocol="stdio", target="npx -y some-server",
        config={"env": {"API_KEY": "v-API-KEY-VALUE"}},
    )
    assert entry.config == {"env": {"API_KEY": "v-API-KEY-VALUE"}}


async def test_mcp_protocol_field_rules(mcp_db):
    """protocol 仅 kind=mcp 有意义：三种传输合法、缺省 streamable_http、其余 kind 拒。"""
    for name, protocol in (("p-sse", "sse"), ("p-http", "streamable_http")):
        entry = await _save(name=name, kind="mcp", protocol=protocol, target="https://x")
        assert entry.protocol == protocol
    # 缺省 ⇒ streamable_http；headers 配 HTTP 传输合法。
    default_entry = await _save(
        name="p-default", target="https://x",
        config={"headers": {"Authorization": "Bearer v-TEST-TOKEN"}},
    )
    assert default_entry.protocol == "streamable_http"

    with pytest.raises(TogoException) as bad_value:
        await _save(name="p-bad", kind="mcp", protocol="websocket", target="https://x")
    assert bad_value.value.error_code == "mcp_invalid_protocol"

    with pytest.raises(TogoException) as on_non_mcp:
        await _save(name="p-openapi", kind="openapi", protocol="sse", target="./spec.json")
    assert on_non_mcp.value.error_code == "mcp_invalid_protocol"

    # 非 mcp kind 的 protocol 归一为 NULL（不留空串）。
    plain = await _save(name="p-plain", kind="openapi", target="./spec.json")
    assert plain.protocol is None


# ---------------------------------------------------------------------------
# 唯一索引 / overwrite / rm（全局唯一，#273）
# ---------------------------------------------------------------------------


async def test_second_add_without_overwrite_refused(mcp_db):
    await _save(target="https://mcp.context7.com/mcp?api_key=TOPSECRET")
    with pytest.raises(TogoException) as excinfo:
        await _save(kind="openapi", target="https://other.example.com")
    assert excinfo.value.error_code == "mcp_name_exists"
    message = str(excinfo.value)
    # 现有条目摘要按 §4.5 打码：query/长 token 不回显，可读段（域名）允许。
    assert "TOPSECRET" not in message, "报错里不得出现 target 内嵌凭证值"
    assert "https://mcp.context7.com/mcp?***" in message


async def test_unique_index_is_the_real_backstop(mcp_db):
    """并发第二写直接撞全局唯一索引（绕过 service 预检也拦得住）。"""
    row = GtMcpServer(name="raw", kind="mcp", target="https://a")
    await row.aio_save(force_insert=True)
    duplicate = GtMcpServer(name="raw", kind="mcp", target="https://b")
    with pytest.raises(peewee.IntegrityError):
        await duplicate.aio_save(force_insert=True)


async def test_registry_is_global_not_team_scoped(mcp_db):
    """#273：注册表不分 team——service 层已无 team_id 参数，同一库只有一份列表。"""
    await _save(name="ctx7")
    await _save(name="github-mcp")
    rows = await svc.list_servers()
    assert [r.name for r in rows] == ["ctx7", "github-mcp"]
    # model 层也没有 team_id 字段可查（字段被删，属结构级证明）。
    assert not hasattr(GtMcpServer, "team_id")


async def test_overwrite_updates_existing_row(mcp_db):
    await _save()
    updated = await _save(
        kind="openapi",
        target="https://spec.example.com/openapi.json",
        note="换目标",
        config={"headers": {"Authorization": "Bearer T-value"}},
        overwrite=True,
    )
    assert updated.id == (await gtMcpServerManager.get_by_name("ctx7")).id
    rows = await svc.list_servers()
    assert len(rows) == 1, "覆盖不得产生第二条记录"
    fresh = rows[0]
    assert fresh.kind == "openapi"
    assert fresh.target == "https://spec.example.com/openapi.json"
    assert fresh.config == {"headers": {"Authorization": "Bearer T-value"}}


async def test_overwrite_does_not_touch_enable(mcp_db):
    """覆盖登记只改 kind/target/note/config，不动运维态 enable（#273）。"""
    await _save()
    await svc.set_enabled("ctx7", False)
    await _save(kind="openapi", target="https://spec.example.com/openapi.json", overwrite=True)
    fresh = await gtMcpServerManager.get_by_name("ctx7")
    assert fresh.enable == 0, "overwrite 不得偷偷把禁用的条目重新启用"


async def test_delete_removes_exactly_one_row(mcp_db):
    await _save()
    removed = await svc.delete_server("ctx7")
    assert removed.name == "ctx7"
    assert await gtMcpServerManager.get_by_name("ctx7") is None

    with pytest.raises(TogoException) as excinfo:
        await svc.delete_server("ctx7")
    assert excinfo.value.error_code == "mcp_name_not_found"


# ---------------------------------------------------------------------------
# enable 三态（#273）
# ---------------------------------------------------------------------------


async def test_set_enabled_toggles_and_not_found(mcp_db):
    await _save()
    disabled = await svc.set_enabled("ctx7", False)
    assert disabled.enable == 0
    enabled = await svc.set_enabled("ctx7", True)
    assert enabled.enable == 1

    with pytest.raises(TogoException) as excinfo:
        await svc.set_enabled("nope", False)
    assert excinfo.value.error_code == "mcp_name_not_found"


async def test_disabled_entry_still_listed_and_resolvable(mcp_db):
    """禁用 ≠ 删除：list 仍列出、resolve 仍命中（禁用判断由调用方做）。"""
    await _save()
    await svc.set_enabled("ctx7", False)
    names = [r.name for r in await svc.list_servers()]
    assert names == ["ctx7"], "禁用条目必须仍可见"
    hit = await svc.resolve_name("ctx7")
    assert hit is not None and hit.enable == 0


async def test_entry_summary_masks_target_and_flags_config(mcp_db):
    await _save(
        target="https://api.example.com/v1?api_key=should_never_leak",
        config={"headers": {"Authorization": "env:GITHUB_TOKEN"}},
    )
    entry = await gtMcpServerManager.get_by_name("ctx7")
    summary = svc.entry_summary(entry)
    assert summary["name"] == "ctx7"
    assert summary["enable"] is True
    assert "should_never_leak" not in json_dumps(summary)
    assert summary["has_config"] is True
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


def test_mask_config_keeps_prefix_only():
    """#277：值已明文，展示掩码=保留少量前缀 + ***；完整值任何形态都不得出现。"""
    masked = mask_config_for_display(
        {"headers": {"Authorization": "Bearer sk-TESTONLY-1234567890"}, "env": {"API_KEY": "v-SECRET-999"}}
    )
    assert masked == {"headers": {"Authorization": "Bearer***"}, "env": {"API_KEY": "v-SECRET***"}}
    # 完整值不得出现在掩码里
    assert "sk-TESTONLY" not in json_dumps(masked)
    assert "v-SECRET-999" not in json_dumps(masked)
    # 短值整段打码
    assert mask_config_for_display({"headers": {"A": "abc"}}) == {"headers": {"A": "***"}}
    assert mask_config_for_display({}) == {}


async def test_model_to_json_masks_values(mcp_db):
    """to_json 展示面：完整明文值不出现在 JSON 里，凭证字段是掩码形态（#277）。"""
    await _save(
        target="https://api.example.com/v1?api_key=should_never_leak",
        config={"headers": {"Authorization": "Bearer sk-TESTONLY-1234567890"}},
    )
    entry = await gtMcpServerManager.get_by_name("ctx7")
    data = entry.to_json()
    dumped = json_dumps(data, default=str)
    assert "should_never_leak" not in dumped
    assert "sk-TESTONLY-1234567890" not in dumped, "明文凭证值不得出现在 to_json 展示面"
    assert data["config"] == {"headers": {"Authorization": "Bearer***"}}
    assert "target" not in data and data["target_masked"].endswith("?***")


async def test_config_of_returns_verbatim_values(mcp_db):
    """#277：`config_of` 取出的是**原样值**（注入直接用字面值，无解析链）。"""
    await _save(
        config={"headers": {"Authorization": "Bearer sk-TESTONLY-1234567890"}},
    )
    entry = await gtMcpServerManager.get_by_name("ctx7")
    creds = svc.config_of(entry)
    assert creds == {"headers": {"Authorization": "Bearer sk-TESTONLY-1234567890"}, "env": {}}
