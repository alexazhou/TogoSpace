"""容器生命周期编排：配额、命名、目标解析、gtsp 注入与清理。

设计依据：docs/design/2026-10-31-docker-agent-sandbox/step2_technical.md §3 / §5。

**这一层只做编排，不打开 stdio 连接**：真正「进入容器」是 `TspAgentDriver` 的事
（第 4 步）。本模块负责把「容器存在且可执行」、「库里有这条记录」两件事对齐。

几个容易踩错、且都是设计要求（不是实现口味）的点：
1. **先落预记录再 docker create**（§5.3）：`docker_id=NULL` 的预记录同时占住
   配额与容器名，否则并发创建能绕过 `MAX_CONTAINERS_PER_AGENT`。
2. **自建容器创建后立刻 `docker cp` 注入 gtsp**：`--entrypoint` 指向 `/tmp/gtsp`，
   容器内本来没有这个文件；注入失败要连带删容器 + 物理删除预记录。
3. **补偿删除一律按 `docker_id`，只有 `docker_id IS NULL` 的预记录才按
   `container_name` + label 校验后删**：按名删外部同名容器是不可接受的破坏性操作，
   这是 P-1 风险的对策（§3.5 / §5.4）。
4. **创建成功不切环境**：`create_container` 只在宿主侧准备好容器，Agent 仍是宿主
   环境，要显式 `enter_container` 才切换（§6.2 第 1 条）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from constants import (
    CONTAINER_CREATE_TIMEOUT_SEC,
    CONTAINER_GTSP_PATH,
    CONTAINER_INSPECT_TIMEOUT_SEC,
    CONTAINER_LABEL_RECORD_ID,
    CONTAINER_LOG_DIR,
    CONTAINER_SELF_MOUNT_PATH,
    CONTAINER_SHARED_MOUNT_PATH,
    DEFAULT_CONTAINER_IMAGE,
    MAX_CONTAINERS_PER_AGENT,
)
from dal.db import gtAgentContainerManager
from exception import TogoException
from model.coreModel import (
    CONTAINER_STATE_MISSING,
    CONTAINER_STATE_UNKNOWN,
    ContainerInfo,
    ContainerTarget,
)
from model.dbModel.gtAgentContainer import GtAgentContainer

import appPaths
from service.containerService.dockerCli import DockerCli, DockerCliError
from util import workspaceUtil

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 错误码（§3.6）
# ---------------------------------------------------------------------------

ERROR_DOCKER_CLI_NOT_FOUND = "docker_cli_not_found"
ERROR_DOCKER_DAEMON_UNAVAILABLE = "docker_daemon_unavailable"
ERROR_CONTAINER_MOUNT_DENIED = "container_mount_denied"
ERROR_CONTAINER_IMAGE_MISSING = "container_image_missing"
ERROR_CONTAINER_IMAGE_NOT_LINUX = "container_image_not_linux"
ERROR_CONTAINER_ARCH_UNSUPPORTED = "container_arch_unsupported"
ERROR_CONTAINER_NOT_FOUND = "container_not_found"
ERROR_CONTAINER_ALREADY_RUNNING = "container_already_running"
ERROR_CONTAINER_IN_USE = "container_in_use"
ERROR_CONTAINER_STOPPED = "container_stopped"
ERROR_CONTAINER_CREATE_FAILED = "container_create_failed"
ERROR_CONTAINER_ENTER_FAILED = "container_enter_failed"
ERROR_CONTAINER_NOT_EXECUTABLE = "container_not_executable"
ERROR_CONTAINER_LIMIT_REACHED = "container_limit_reached"
ERROR_CONTAINER_DELETE_FAILED = "container_delete_failed"
ERROR_CONTAINER_INVALID_PARAMS = "container_invalid_params"

# 容器 ID 必须是 12 位十六进制短 ID（§6.3 严格 ID 传参）。
# 拒绝镜像名：`docker inspect` 接受镜像名，若放行会让 Agent 拿镜像名当容器 ID。
_SHORT_ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")

# 容器名允许的字符：Agent 名可能是任意 Unicode，但容器名只能用小写字母数字
# 与 `_.-`（Docker 限制）。不合法时回退到 `agent-<agent_id>-<index>`（§3.5）。
_CONTAINER_NAME_SAFE = re.compile(r"[^a-z0-9_.-]+")

# 创建后立即注入时的容器名回退前缀
_FALLBACK_NAME_PREFIX = "agent"

# 挂载来源不可用/Docker Desktop 未共享目录时的 stderr 特征
_MOUNT_DENIED_MARKERS = (
    "is not shared from the host",
    "file sharing",
    "mounts denied",
    "operation not permitted",
)

_cli: DockerCli | None = None


def get_cli() -> DockerCli:
    """惰性创建并复用 DockerCli 单例。

    不在模块导入期创建：`DockerCli()` 在 docker 不存在时会抛错，而本模块会被
    `funcToolService` 间接导入，导入期抛错会让整个后端起不来。docker 缺失属于
    运行期环境问题，应当在使用时报错（`docker_cli_not_found`）。
    """
    global _cli
    if _cli is None:
        _cli = DockerCli()
    return _cli


def reset_cli_for_test(cli: DockerCli | None = None) -> None:
    """测试钩子：替换 / 清空单例（生产代码不要调用）。"""
    global _cli
    _cli = cli


# ---------------------------------------------------------------------------
# 命名与参数构造（纯函数，便于单测钉住 argv 形状）
# ---------------------------------------------------------------------------


def is_short_id(value: str) -> bool:
    """是否为 12 位十六进制短 ID。"""
    return isinstance(value, str) and bool(_SHORT_ID_PATTERN.fullmatch(value))


def build_container_name(agent_id: int, agent_name: str, index: int) -> str:
    """生成容器名 `agent-<name>-<index>`，非法字符回退为 `agent-<agent_id>-<index>`。

    Agent 名可以含中文、空格、emoji（`validate_dir_name` 只禁了路径分隔符等），
    但 Docker 容器名必须是 `[a-zA-Z0-9][a-zA-Z0-9_.-]*`。因此：
    - 先小写化并把非法字符压成 `-`；
    - 若结果被削空（如纯中文名 → 全被替换），回退到 agent_id，保证唯一且合法。

    **长名不截断**：`container_name` 列参与部分唯一索引，截断会让两个不同 Agent
    撞名；Docker 对容器名长度本身没有限制（它不是 hostname）。

    Args:
        agent_id: Agent ID（回退名与唯一性来源）
        agent_name: Agent 名称（可含任意 Unicode）
        index: 名称后缀编号 1..3

    Returns:
        合法的 Docker 容器名，形如 `agent-xiao-mei-1` 或 `agent-7-1`。
    """
    sanitized = _CONTAINER_NAME_SAFE.sub("-", (agent_name or "").strip().lower())
    # 修剪首尾的分隔符（Docker 要求以字母数字开头）
    sanitized = sanitized.strip("-._")
    if not sanitized:
        sanitized = str(agent_id)
    return f"{_FALLBACK_NAME_PREFIX}-{sanitized}-{index}"


def pick_free_index(active_names: list[str], agent_id: int, agent_name: str) -> int | None:
    """在 1..MAX 中选未被占用的最小编号；全占满返回 None（交给调用方报超限）。"""
    used: set[int] = set()
    for index in range(1, MAX_CONTAINERS_PER_AGENT + 1):
        if build_container_name(agent_id, agent_name, index) in active_names:
            used.add(index)
    for index in range(1, MAX_CONTAINERS_PER_AGENT + 1):
        if index not in used:
            return index
    return None


def build_create_argv(
    *,
    container_name: str,
    record_id: int,
    image: str,
    agent_workdir: str,
    shared_workdir: str,
) -> list[str]:
    """构造 `docker create` 的完整 argv（§3.5）。

    硬性约束（每条都对应一次实测踩坑）：
    - **必须带 `-i`，绝不带 `-t`**：gtsp 走 stdio，伪终端会改写原始流；
    - **`--entrypoint` 必须置于镜像名之前**：放在镜像名后会被当成容器的 CMD；
    - **不传 `--workdir`**：跟随镜像默认 WORKDIR；
    - **不传 `--read-only`**：gtsp 需要写 `/tmp`；
    - **恒开 `--init`**：由 tini 回收孤儿僵尸进程，且 gtsp 退出时同步退出；
    - **必须显式传可写的 `--log-path`**：镜像默认没有 `/tmp/togospace/logs` 之外
      的约定路径；
    - 镜像名之后是容器 CMD（gtsp 的启动参数）。
    """
    return [
        "create",
        "--name", container_name,
        "--label", f"{CONTAINER_LABEL_RECORD_ID}={record_id}",
        "-i",
        "--init",
        "--entrypoint", CONTAINER_GTSP_PATH,
        "--mount", f"type=bind,src={agent_workdir},dst={CONTAINER_SELF_MOUNT_PATH}",
        "--mount",
        f"type=bind,src={shared_workdir},dst={CONTAINER_SHARED_MOUNT_PATH},readonly",
        "--env", "HOME=/tmp",
        "--network", "bridge",
        "--security-opt", "no-new-privileges",
        image,
        "--mode", "stdio",
        "--log-path", CONTAINER_LOG_DIR,
    ]


def build_container_start_argv(docker_id: str) -> list[str]:
    """自建容器启动 argv：`docker start -a -i <id>`。

    `-a`（attach）把容器 stdio 接到当前进程；`-i` 保持 stdin 打开。
    关掉 stdin 后容器内 gtsp 收到 EOF 退出 → tini 同步退出 → 容器回 `exited`。
    """
    return ["start", "-a", "-i", docker_id]


def build_container_exec_argv(docker_id: str) -> list[str]:
    """外部容器启动 argv：`docker exec -i <id> /tmp/gtsp --mode stdio ...`。

    外部容器的主进程必须保持运行，所以只能 `exec` 进一个额外进程，
    不能用 `start`。
    """
    return [
        "exec",
        "-i",
        docker_id,
        CONTAINER_GTSP_PATH,
        "--mode", "stdio",
        "--log-path", CONTAINER_LOG_DIR,
    ]


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _classify_docker_error(exc: DockerCliError) -> str:
    """把 docker 的 stderr 归到 §3.6 的错误码；无法归类时给通用失败码。"""
    if exc.error_code in (ERROR_DOCKER_CLI_NOT_FOUND, ERROR_DOCKER_DAEMON_UNAVAILABLE):
        return exc.error_code
    lowered = (exc.stderr or "").lower()
    if any(marker in lowered for marker in _MOUNT_DENIED_MARKERS):
        return ERROR_CONTAINER_MOUNT_DENIED
    if "no such image" in lowered or "pull access denied" in lowered:
        return ERROR_CONTAINER_IMAGE_MISSING
    if "no such container" in lowered or "no such object" in lowered:
        return ERROR_CONTAINER_NOT_FOUND
    return ERROR_CONTAINER_CREATE_FAILED


async def _resolve_agent_names(agent_id: int) -> tuple[int, str, str, str]:
    """解析创建容器需要的 Agent 信息：`(team_id, agent_name, agent_workdir, shared_workdir)`。

    工作目录必须在这里按当前名字重新算：`bind mount` 在创建时固化路径，
    Agent 改名后旧路径失效（§7）。
    """
    from dal.db import gtAgentManager, gtTeamManager

    gt_agent = await gtAgentManager.get_agent_by_id(agent_id)
    if gt_agent is None:
        raise TogoException(f"Agent 不存在: agent_id={agent_id}", ERROR_CONTAINER_CREATE_FAILED)

    gt_team = await gtTeamManager.get_team_by_id(gt_agent.team_id)
    if gt_team is None:
        raise TogoException(
            f"Agent 所属 Team 不存在: team_id={gt_agent.team_id}",
            ERROR_CONTAINER_CREATE_FAILED,
        )

    team_workdir = workspaceUtil.resolve_team_workdir(
        gt_team.name, gt_team.config, workspaceUtil.resolve_workspace_root(None)
    )
    agent_workdir = workspaceUtil.resolve_agent_workdir(team_workdir, gt_agent.name)
    shared_workdir = workspaceUtil.resolve_shared_workdir(team_workdir)
    return gt_agent.team_id, gt_agent.name, agent_workdir, shared_workdir


async def _inspect_container(cli: DockerCli, ref: str) -> dict | None:
    """`docker inspect` 单个容器，返回 JSON 首元素；容器不存在返回 None。

    用 `--format "{{json .}}"` 避免解析整个数组。inspect **失败**（daemon 不可用、
    超时）与「容器不存在」必须区分：前者抛 `DockerCliError`，后者返回 None。
    """
    try:
        result = await cli.run(
            ["inspect", "--type", "container", "--format", "{{json .}}", ref],
            timeout_sec=CONTAINER_INSPECT_TIMEOUT_SEC,
        )
    except DockerCliError as exc:
        if "no such" in (exc.stderr or "").lower():
            return None
        raise

    text = (result.stdout or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("docker inspect 输出无法解析: ref=%s", ref)
        return None


def _state_of(inspect_data: dict | None) -> str:
    """取 `State.Status` 原值；不存在为 missing。"""
    if inspect_data is None:
        return CONTAINER_STATE_MISSING
    state = (inspect_data.get("State") or {}).get("Status")
    return str(state) if state else CONTAINER_STATE_UNKNOWN


async def _resolve_image_arch(cli: DockerCli, image_ref: str) -> str:
    """按镜像 ID 查架构，返回 amd64 / arm64；不支持时抛 `container_arch_unsupported`。

    容器 inspect 的 `.Platform` 只有 OS（如 `linux`），**没有架构**，
    必须先取 `.Image`（镜像 ID）再 `docker image inspect`（§3.2 实测结论）。
    """
    try:
        result = await cli.run(
            ["image", "inspect", "--format", "{{.Architecture}}", image_ref],
            timeout_sec=CONTAINER_INSPECT_TIMEOUT_SEC,
        )
    except DockerCliError as exc:
        if "no such" in (exc.stderr or "").lower():
            raise TogoException(
                f"镜像不存在: {image_ref}", ERROR_CONTAINER_IMAGE_MISSING
            ) from exc
        raise TogoException(
            f"查询镜像架构失败: {exc.error_message}", ERROR_CONTAINER_ENTER_FAILED
        ) from exc

    normalized = appPaths.normalize_container_arch(result.stdout.strip())
    if normalized is None:
        raise TogoException(
            f"不支持的镜像架构: {result.stdout.strip()}",
            ERROR_CONTAINER_ARCH_UNSUPPORTED,
        )
    return normalized


async def _resolve_image_os(cli: DockerCli, image_ref: str) -> str:
    """查镜像 OS；非 linux 直接拒绝（Windows 镜像不能跑 Linux gtsp）。"""
    result = await cli.run(
        ["image", "inspect", "--format", "{{.Os}}", image_ref],
        timeout_sec=CONTAINER_INSPECT_TIMEOUT_SEC,
    )
    return result.stdout.strip().lower()


# ---------------------------------------------------------------------------
# 对外接口（§3.4）
# ---------------------------------------------------------------------------


async def _next_container_name(agent_id: int, agent_name: str) -> tuple[int, str]:
    """取该 Agent 当前活跃记录里未被占用的最小编号，并生成容器名。

    配额校验与编号选择必须和预记录插入在同一互斥区内（由调用方的锁保证），
    否则并发创建会选到同一个编号，撞 `container_name` 部分唯一索引。
    """
    active = await gtAgentContainerManager.list_active_by_agent(agent_id)
    index = pick_free_index([row.container_name for row in active], agent_id, agent_name)
    if index is None:
        raise TogoException(
            f"自建容器数已达上限（{MAX_CONTAINERS_PER_AGENT}）",
            ERROR_CONTAINER_LIMIT_REACHED,
        )
    return index, build_container_name(agent_id, agent_name, index)


async def create_container(agent_id: int, image: str | None = None) -> GtAgentContainer:
    """创建自建容器并注入 gtsp，返回已回填 `docker_id` 的记录。

    **只创建，不切换执行环境**（§6.2 第 1 条）：Agent 仍在宿主，
    要 `enter_container` 才真正进去。

    失败补偿（§5.3）：`docker create` / 回填 / 注入任一步失败，都先
    `docker rm -f` 删掉本次创建的容器（若已创建），再**物理删除**预记录，
    不留下占配额的行。

    Args:
        agent_id: 所属 Agent
        image: 镜像名；None / 空串走 `DEFAULT_CONTAINER_IMAGE`

    Raises:
        TogoException: 配额超限、镜像不可用、创建或注入失败
    """
    image = (image or "").strip() or DEFAULT_CONTAINER_IMAGE
    cli = get_cli()

    _team_id, agent_name, agent_workdir, shared_workdir = await _resolve_agent_names(agent_id)

    # 配额先在**任何 docker 调用之前**检查：超限是最常见的失败，不该先花两次
    # docker 调用去探测镜像再报错（探测镜像要拉 daemon，可能几百毫秒）。
    active = await gtAgentContainerManager.list_active_by_agent(agent_id)
    if len(active) >= MAX_CONTAINERS_PER_AGENT:
        raise TogoException(
            f"自建容器数已达上限（{MAX_CONTAINERS_PER_AGENT}）",
            ERROR_CONTAINER_LIMIT_REACHED,
        )

    # 镜像校验放在落库之前：镜像不存在/非 Linux/架构不支持时不许产生任何 DB 记录
    try:
        image_os = await _resolve_image_os(cli, image)
    except DockerCliError as exc:
        raise TogoException(
            f"查询镜像失败: {exc.error_message}", _classify_docker_error(exc)
        ) from exc
    if image_os and image_os != "linux":
        raise TogoException(
            f"镜像 {image} 不是 Linux 镜像（OS={image_os}），无法运行 Linux gtsp",
            ERROR_CONTAINER_IMAGE_NOT_LINUX,
        )
    await _resolve_image_arch(cli, image)

    # 配额 + 命名 + 预记录：同一把按 Agent 的锁内完成（避免并发绕过上限）。
    # 上面的配额检查是无锁快路径（省一次锁争用）；这里在锁内**再查一次**，
    # 并发创建时以锁内结果为准。
    async with _agent_lock(agent_id):
        active = await gtAgentContainerManager.list_active_by_agent(agent_id)
        if len(active) >= MAX_CONTAINERS_PER_AGENT:
            raise TogoException(
                f"自建容器数已达上限（{MAX_CONTAINERS_PER_AGENT}）",
                ERROR_CONTAINER_LIMIT_REACHED,
            )
        index = pick_free_index([row.container_name for row in active], agent_id, agent_name)
        if index is None:
            # 理论上与上面的 len 判断等价；保留以防御「名称被占但行数未满」
            raise TogoException(
                f"自建容器数已达上限（{MAX_CONTAINERS_PER_AGENT}）",
                ERROR_CONTAINER_LIMIT_REACHED,
            )
        container_name = build_container_name(agent_id, agent_name, index)
        record = await gtAgentContainerManager.create(
            agent_id=agent_id,
            container_name=container_name,
            image=image,
        )

    record_id = record.id
    assert record_id is not None

    # 从这一步起任何失败都要补偿：删容器（若已建）+ 物理删预记录
    docker_id: str | None = None
    try:
        argv = build_create_argv(
            container_name=container_name,
            record_id=record_id,
            image=image,
            agent_workdir=agent_workdir,
            shared_workdir=shared_workdir,
        )
        try:
            created = await cli.run(argv, timeout_sec=CONTAINER_CREATE_TIMEOUT_SEC)
        except DockerCliError as exc:
            raise TogoException(
                f"创建容器失败: {exc.error_message}", _classify_docker_error(exc)
            ) from exc

        full_id = (created.stdout or "").strip()
        if len(full_id) < 12:
            raise TogoException(
                f"docker create 返回的容器 ID 异常: {full_id!r}",
                ERROR_CONTAINER_CREATE_FAILED,
            )
        docker_id = full_id[:12]
        await gtAgentContainerManager.set_docker_id(record, docker_id)

        # 注入口径：自建容器创建后立即注入（§3.2）
        await _inject_gtsp(cli, docker_id, image)
    except BaseException:
        # 补偿：先删容器（按 docker_id，绝不用名字去 rm 别人的容器），
        # 再物理删除预记录（硬删除：不留占配额的行）。
        await _cleanup_failed_create(cli, docker_id, record)
        raise

    logger.info(
        "容器创建完成: agent_id=%s, record_id=%s, docker_id=%s, name=%s, image=%s",
        agent_id, record_id, docker_id, container_name, image,
    )
    return record


async def _cleanup_failed_create(
    cli: DockerCli, docker_id: str | None, record: GtAgentContainer
) -> None:
    """创建失败的补偿清理。任何异常都吞掉并记 warning，不能盖住原始错误。"""
    if docker_id:
        try:
            await cli.run(["rm", "-f", docker_id], timeout_sec=CONTAINER_CREATE_TIMEOUT_SEC)
        except Exception:
            logger.warning(
                "补偿删除容器失败（需人工检查）: docker_id=%s", docker_id, exc_info=True
            )
    try:
        await gtAgentContainerManager.hard_delete(record)
    except Exception:
        logger.warning(
            "补偿删除预记录失败: record_id=%s", record.id, exc_info=True
        )


async def _inject_gtsp(cli: DockerCli, docker_id: str, image: str) -> None:
    """`docker cp` 注入 Linux gtsp 到 `/tmp/gtsp`（§3.2）。

    权限由源文件 `0755` 保证（`docker cp` 保留权限位），**不做容器内 chmod**
    （自建容器在 created / exited 状态无法 exec）。

    `docker cp` 不补父目录：镜像没有 `/tmp` 时注入必然失败，按
    `container_not_executable` 处理，不做 staging 兜底（§3.2）。
    """
    arch = await _resolve_image_arch(cli, image)
    local_binary = appPaths.get_gtsp_linux_binary_path(arch)

    try:
        await cli.run(
            ["cp", local_binary, f"{docker_id}:{CONTAINER_GTSP_PATH}"],
            timeout_sec=CONTAINER_CREATE_TIMEOUT_SEC,
        )
    except DockerCliError as exc:
        raise TogoException(
            "gtsp 注入失败：容器内 /tmp 不存在或不可写，或镜像不可执行二进制。"
            f"原始错误: {exc.error_message}",
            ERROR_CONTAINER_NOT_EXECUTABLE,
        ) from exc


async def get_container_target(agent_id: int, container_id: str) -> ContainerTarget:
    """解析容器连接目标：自建容器优先，否则按外部容器处理（§3.4）。

    Args:
        agent_id: 发起方 Agent（只用于自建容器的归属判定）
        container_id: 12 位十六进制短 ID

    Raises:
        TogoException: 参数非法 / 容器不存在 / inspect 失败 / 架构不支持
    """
    if not is_short_id(container_id):
        raise TogoException(
            "container_id 必须是 12 位十六进制容器短 ID（不接受容器名或镜像名）",
            ERROR_CONTAINER_INVALID_PARAMS,
        )

    cli = get_cli()

    # 自建容器：`agent_id + docker_id` 精确命中即视为 managed
    record = await gtAgentContainerManager.get_active_by_docker_id(agent_id, container_id)
    if record is not None:
        try:
            inspect_data = await _inspect_container(cli, container_id)
        except DockerCliError as exc:
            raise TogoException(
                f"查询容器状态失败: {exc.error_message}",
                _classify_docker_error(exc),
            ) from exc
        if inspect_data is None:
            # 库里有记录但容器没了：只报错，不在这里软删除（§5.1）
            raise TogoException(
                f"容器 {container_id} 已不存在（记录仍保留，可用 delete_container 清理）",
                "container_lost",
            )
        image_id = inspect_data.get("Image")
        if not image_id:
            raise TogoException(
                f"无法获取容器 {container_id} 的镜像 ID", ERROR_CONTAINER_ENTER_FAILED
            )
        arch = await _resolve_image_arch(cli, image_id)
        return ContainerTarget(docker_id=container_id, is_managed=True, arch=arch)

    # 外部容器：必须真实存在且 running
    try:
        inspect_data = await _inspect_container(cli, container_id)
    except DockerCliError as exc:
        raise TogoException(
            f"查询容器状态失败: {exc.error_message}",
            _classify_docker_error(exc),
        ) from exc
    if inspect_data is None:
        raise TogoException(
            f"容器 {container_id} 不存在", ERROR_CONTAINER_NOT_FOUND
        )

    image_id = inspect_data.get("Image")
    if not image_id:
        raise TogoException(
            f"无法获取容器 {container_id} 的镜像 ID", ERROR_CONTAINER_ENTER_FAILED
        )
    arch = await _resolve_image_arch(cli, image_id)
    return ContainerTarget(docker_id=container_id, is_managed=False, arch=arch)


async def prepare_container_gtsp(target: ContainerTarget) -> None:
    """进入外部容器前注入 gtsp（自建容器在创建时已注入，重复调用是幂等的）。

    重复注入本身无害（`docker cp` 覆盖同名文件），但对自建容器没必要，
    这里只在外部容器上执行，避免多一次 6MB 拷贝。
    """
    if target.is_managed:
        return
    cli = get_cli()
    local_binary = appPaths.get_gtsp_linux_binary_path(target.arch)
    try:
        await cli.run(
            ["cp", local_binary, f"{target.docker_id}:{CONTAINER_GTSP_PATH}"],
            timeout_sec=CONTAINER_CREATE_TIMEOUT_SEC,
        )
    except DockerCliError as exc:
        raise TogoException(
            "gtsp 注入失败：容器内 /tmp 不存在或不可写，或镜像不可执行二进制。"
            f"原始错误: {exc.error_message}",
            ERROR_CONTAINER_NOT_EXECUTABLE,
        ) from exc


# 按 Agent 的创建互斥锁：配额校验 → 选编号 → 插预记录必须在同一临界区内。
# 锁只在进程内有效；同一 STORAGE_ROOT 多后端不并行是本项目的既有假设（§12）。
_agent_locks: dict[int, asyncio.Lock] = {}


class _agent_lock:
    """按 agent_id 的异步互斥锁（上下文管理器）。

    创建容器是「读配额 → 改配额」的复合操作，必须串行化。用 dict 按 Agent 分锁
    而不是全局一把，避免一个 Agent 的慢创建阻塞其他人的容器操作。
    """

    def __init__(self, agent_id: int) -> None:
        self._agent_id = agent_id

    async def __aenter__(self) -> None:
        lock = _agent_locks.get(self._agent_id)
        if lock is None:
            lock = asyncio.Lock()
            _agent_locks[self._agent_id] = lock
        self._lock = lock
        await self._lock.acquire()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._lock.release()


async def list_agent_containers(
    agent_id: int,
    *,
    current_container_id: str | None = None,
    docker_available: bool | None = None,
) -> list[ContainerInfo]:
    """列出该 Agent 全部活跃自建容器，并实时填状态（§3.4 / §8）。

    两类记录的取状态方式不同：
    - 已回填 `docker_id`：按 `docker_id` inspect；
    - 预记录（`docker_id IS NULL`）：按 `container_name` inspect，**并且必须校验
      label**。不校验就会把宿主上同名的外部容器当成自己的（§2.1 预记录可见性）。

    inspect 是并发发的：3 个容器串行 3s 超时会卡 9s，列表接口不能这么慢。

    Args:
        agent_id: 目标 Agent
        current_container_id: 该 Agent 当前所处容器（用于 `is_entered`）
        docker_available: 外部已探测的 daemon 可用性；None 表示本函数自己探测

    Returns:
        按 `record_id` 升序的容器信息列表（含预记录，`docker_id` 为 None）。
    """
    cli = get_cli()
    records = await gtAgentContainerManager.list_active_by_agent(agent_id)

    if docker_available is None:
        docker_available = await cli.ping()

    if not docker_available:
        # daemon 不可用：不把状态伪造成 missing，用 unknown 表示「查不到」
        return [
            ContainerInfo(
                record_id=record.id,
                agent_id=record.agent_id,
                docker_id=record.docker_id,
                container_name=record.container_name,
                image=record.image,
                state=CONTAINER_STATE_UNKNOWN,
                is_entered=record.docker_id is not None
                and record.docker_id == current_container_id,
                created_at=record.created_at,
            )
            for record in records
        ]

    states = await asyncio.gather(
        *(_resolve_record_state(cli, record) for record in records),
        return_exceptions=False,
    )

    return [
        ContainerInfo(
            record_id=record.id,
            agent_id=record.agent_id,
            docker_id=record.docker_id,
            container_name=record.container_name,
            image=record.image,
            state=state,
            is_entered=record.docker_id is not None
            and record.docker_id == current_container_id,
            created_at=record.created_at,
        )
        for record, state in zip(records, states)
    ]


async def _resolve_record_state(cli: DockerCli, record: GtAgentContainer) -> str:
    """解析单条记录的真实状态。

    - 预记录按名 inspect 后**校验 label**：label 不匹配 ⇒ 那个同名容器不是我们
      创建的，对本记录而言就是 `missing`（绝不当成自己的容器）。
    - inspect 本身失败（daemon 抖动 / 超时）⇒ `unknown`，不能据此认定容器已丢。
    """
    ref = record.docker_id or record.container_name
    try:
        inspect_data = await _inspect_container(cli, ref)
    except DockerCliError as exc:
        logger.warning(
            "查询容器状态失败: record_id=%s, ref=%s, error=%s",
            record.id, ref, exc.error_message,
        )
        return CONTAINER_STATE_UNKNOWN

    if inspect_data is None:
        return CONTAINER_STATE_MISSING

    if record.docker_id is None and not _label_matches(inspect_data, record.id):
        logger.info(
            "同名容器 label 不匹配，不视为本记录: record_id=%s, name=%s",
            record.id, record.container_name,
        )
        return CONTAINER_STATE_MISSING

    return _state_of(inspect_data)


def _label_matches(inspect_data: dict, record_id: int) -> bool:
    """校验容器的 `com.togospace.container_record_id` label 是否等于本记录 ID。"""
    config = inspect_data.get("Config") or {}
    labels = config.get("Labels") or {}
    return str(labels.get(CONTAINER_LABEL_RECORD_ID, "")) == str(record_id)


async def delete_container(
    agent_id: int,
    *,
    container_id: str | None = None,
    record_id: int | None = None,
    current_container_id: str | None = None,
) -> GtAgentContainer:
    """删除自建容器并软删除记录（§5.4）。

    两种寻址方式二选一：`container_id`（12 位短 ID）或 `record_id`（表主键，
    预记录只能用这个）。两者都传或都不传都报 `container_invalid_params`。

    删除顺序是「先 rm 再标记软删除」，且 `rm` 报「容器不存在」视为已删除。
    预记录走 `container_name` + label 校验后 best-effort `rm`：
    label 不匹配时**只处理 DB 记录，绝不 rm**——这是防误删外部同名容器的关键。

    Args:
        agent_id: 发起方 Agent（归属校验）
        container_id: 12 位短 ID
        record_id: `agent_containers.id`
        current_container_id: 当前所处容器；删除它会先被拒绝（`container_in_use`）

    Raises:
        TogoException: 参数非法 / 记录不存在 / 正在使用 / 删除失败

    Returns:
        已标记 `deleted = 1` 的记录（调用方取 `docker_id` 作为 `deleted_container_id`）
    """
    if (container_id is None) == (record_id is None):
        raise TogoException(
            "container_id 与 record_id 必须二选一", ERROR_CONTAINER_INVALID_PARAMS
        )

    if container_id is not None:
        if not is_short_id(container_id):
            raise TogoException(
                "container_id 必须是 12 位十六进制容器短 ID",
                ERROR_CONTAINER_INVALID_PARAMS,
            )
        record = await gtAgentContainerManager.get_active_by_docker_id(agent_id, container_id)
    else:
        assert record_id is not None
        if record_id <= 0:
            raise TogoException(
                "record_id 必须是正整数", ERROR_CONTAINER_INVALID_PARAMS
            )
        record = await gtAgentContainerManager.get_active_by_id(record_id)
        # 归属校验：别人的记录一律当作不存在（不泄露存在性）
        if record is not None and record.agent_id != agent_id:
            record = None

    if record is None:
        raise TogoException("容器记录不存在或不属于当前 Agent", ERROR_CONTAINER_NOT_FOUND)

    if record.docker_id is not None and record.docker_id == current_container_id:
        raise TogoException(
            "该容器是当前正在使用的执行环境，请先退出到宿主（exit_to_host）再删除",
            ERROR_CONTAINER_IN_USE,
        )

    cli = get_cli()
    if record.docker_id is not None:
        await _rm_container(cli, record.docker_id)
    else:
        await _rm_pre_record_container(cli, record)

    await gtAgentContainerManager.mark_deleted(record)
    logger.info(
        "容器已删除: agent_id=%s, record_id=%s, docker_id=%s",
        agent_id, record.id, record.docker_id,
    )
    return record


async def _rm_container(cli: DockerCli, docker_id: str) -> None:
    """按 `docker_id` 强制删除容器；「容器不存在」视为已达成目标。"""
    try:
        await cli.run(["rm", "-f", docker_id], timeout_sec=CONTAINER_CREATE_TIMEOUT_SEC)
    except DockerCliError as exc:
        if "no such" in (exc.stderr or "").lower():
            # 已被外部删除：目标状态已满足，不报错
            return
        raise TogoException(
            f"删除容器失败: {exc.error_message}", ERROR_CONTAINER_DELETE_FAILED
        ) from exc


async def _rm_pre_record_container(cli: DockerCli, record: GtAgentContainer) -> None:
    """清理预记录对应的容器：按名 inspect + label 校验，匹配才 rm（best-effort）。

    label 不匹配 ⇒ 宿主上那个同名容器不是本记录创建的，**绝不 rm**，
    只继续走 DB 软删除。任何 docker 异常都只记 warning，不阻塞删除流程。
    """
    try:
        inspect_data = await _inspect_container(cli, record.container_name)
    except DockerCliError as exc:
        logger.warning(
            "预记录按名 inspect 失败，跳过容器清理: record_id=%s, name=%s, error=%s",
            record.id, record.container_name, exc.error_message,
        )
        return

    if inspect_data is None:
        return
    if not _label_matches(inspect_data, record.id):
        logger.warning(
            "同名容器 label 不匹配，拒绝删除（可能属于其他程序）: record_id=%s, name=%s",
            record.id, record.container_name,
        )
        return

    try:
        await cli.run(
            ["rm", "-f", record.container_name], timeout_sec=CONTAINER_CREATE_TIMEOUT_SEC
        )
    except DockerCliError as exc:
        logger.warning(
            "预记录容器清理失败（best-effort，继续标记软删除）: record_id=%s, error=%s",
            record.id, exc.error_message,
        )


async def delete_agent_containers(agent_id: int, *, current_container_id: str | None = None) -> int:
    """批量删除某 Agent 的全部活跃自建容器（离职 / 改名 / §7 清理用）。

    best-effort：单个失败只记 warning，继续处理其余记录，最后统一标记软删除。
    返回被标记删除的记录数。当前正在使用的容器会被跳过（由调用方先切回宿主）。
    """
    records = await gtAgentContainerManager.list_active_by_agent(agent_id)
    return await _delete_records(records, current_container_id=current_container_id)


async def delete_team_containers(team_id: int, *, current_container_id: str | None = None) -> int:
    """批量删除某 Team 下全部 Agent 的自建容器（Team 删除 / 改名 / 目录迁移用）。"""
    from dal.db import gtAgentManager

    agents = await gtAgentManager.get_team_all_agents(team_id)
    agent_ids = [agent.id for agent in agents if agent.id is not None]
    if not agent_ids:
        return 0
    records = await gtAgentContainerManager.list_active_by_team_agents(agent_ids)
    return await _delete_records(records, current_container_id=current_container_id)


async def _delete_records(
    records: list[GtAgentContainer], *, current_container_id: str | None
) -> int:
    """批量删除的实现：吞掉全部异常，绝不阻塞调用方的目录/DB 操作（§7）。"""
    if not records:
        return 0

    try:
        cli = get_cli()
    except DockerCliError as exc:
        # docker 都没了：只标记 DB，避免残留记录永久占配额
        logger.warning("批量清理容器时 docker 不可用，仅标记 DB: %s", exc.error_message)
        deleted = 0
        for record in records:
            try:
                if await gtAgentContainerManager.mark_deleted(record):
                    deleted += 1
            except Exception:
                logger.warning("标记容器记录删除失败: record_id=%s", record.id, exc_info=True)
        return deleted

    deleted = 0
    for record in records:
        try:
            if record.docker_id is not None and record.docker_id == current_container_id:
                logger.info("跳过正在使用的容器: record_id=%s", record.id)
                continue
            if record.docker_id is not None:
                await _rm_container(cli, record.docker_id)
            else:
                await _rm_pre_record_container(cli, record)
            if await gtAgentContainerManager.mark_deleted(record):
                deleted += 1
        except Exception:
            logger.warning(
                "删除容器失败（best-effort，继续）: record_id=%s", record.id, exc_info=True
            )
            # rm 失败也标记软删除（§7：避免记录永久占配额）
            try:
                if await gtAgentContainerManager.mark_deleted(record):
                    deleted += 1
            except Exception:
                logger.warning(
                    "标记容器记录删除失败: record_id=%s", record.id, exc_info=True
                )
    return deleted


async def count_containers_by_agent_ids(agent_ids: list[int]) -> dict[int, int]:
    """批量容器数（Agent 列表页 `container_count`，只查 DB 不碰 docker）。"""
    return await gtAgentContainerManager.count_map_by_agent_ids(agent_ids)


# ---------------------------------------------------------------------------
# 进入前的状态判定（§1.2 / §4.1）
# ---------------------------------------------------------------------------

# 自建容器允许拉起的稳态：只有 created / exited。空闲 running 是异常残留
# （gtsp 退出后容器必然停回 exited），隐式 stop 会抹掉排查线索，所以报错而非修正。
_MANAGED_ENTERABLE_STATES = frozenset({"created", "exited"})


def validate_enter_state(target: ContainerTarget, state: str) -> None:
    """校验「现在能不能进入这个容器」，不合法时抛 `TogoException`。

    自建与外部容器的规则**不同**，且不能合并（§4.1 核心不变量）：
    - 自建容器：稳态只有 `created` / `exited` → 拉起；`running` ⇒
      `container_already_running`（空闲 running 属异常残留）；其他状态（paused /
      dead / restarting / removing）⇒ `container_enter_failed`，message 带实际状态。
    - 外部容器：只认 `running`；其余状态 ⇒ `container_stopped`，message 带实际状态。

    这里刻意**不自动修复**（不 unpause、不 restart、不隐式 stop）：文档要求把
    异常状态连同 Docker 原始信息交给 Agent 判断，系统不替它做决定。

    Args:
        target: 连接目标（据 `is_managed` 选规则）
        state: `docker inspect` 得到的 `State.Status` 原值

    Raises:
        TogoException: 状态不允许进入
    """
    if target.is_managed:
        if state in _MANAGED_ENTERABLE_STATES:
            return
        if state == "running":
            raise TogoException(
                "容器当前处于 running 状态，这是异常残留（自建容器空闲时应为 "
                "created 或 exited）。请先排查该容器内是否有残留进程，"
                "或删除后用 create_container 重建。",
                ERROR_CONTAINER_ALREADY_RUNNING,
            )
        raise TogoException(
            f"容器当前状态为 {state}，无法进入（仅 created / exited 可拉起）。"
            "系统不会自动恢复异常状态的容器，请排查或删除后重建。",
            ERROR_CONTAINER_ENTER_FAILED,
        )

    if state == "running":
        return
    raise TogoException(
        f"外部容器当前状态为 {state}，必须先自行启动（docker start）为 running 才能进入。",
        ERROR_CONTAINER_STOPPED,
    )


async def get_container_state(container_id: str) -> str:
    """查容器实时状态；不存在返回 `missing`，inspect 失败返回 `unknown`。

    供进入流程在 `get_container_target` 之后做状态判定用，避免调用方自己拼
    inspect 命令（那样状态语义会有第二份实现）。
    """
    cli = get_cli()
    try:
        inspect_data = await _inspect_container(cli, container_id)
    except DockerCliError as exc:
        logger.warning("查询容器状态失败: id=%s, error=%s", container_id, exc.error_message)
        return CONTAINER_STATE_UNKNOWN
    return _state_of(inspect_data)
