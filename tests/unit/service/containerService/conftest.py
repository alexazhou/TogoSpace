"""`tests/unit/service/containerService` 共用的测试夹具。

**这里放宽的不是产品行为，是「给真 docker 定的时延预算」**（#124）。

## 为什么需要

本目录的单测全部用**假 docker 脚本**驱动真实子进程（`/bin/sh` 或 python 脚本），
不 exec 真 docker。而 `core` 里注入给 `dockerCli.run()` 的超时是**按真 docker 的时延
特征定的**：

| 常量 | 生产值 | 定这个值的理由 |
|---|---|---|
| `CONTAINER_INSPECT_TIMEOUT_SEC` | **3s** | `inspect` 跑在列表接口路径上，daemon 卡死必须快速失败，不能让人看着转圈 |
| `CONTAINER_CREATE_TIMEOUT_SEC` | 60s | `create` + `cp` 天然慢，给足 |

单测验的是**编排语义**（传了什么 argv、失败后补偿了什么、状态怎么归类），
**不是时延**。拿 3s 去卡一个假进程，等于把「性能预算」当成了「语义断言」的一部分。

## 具体症状（#124 实测，可复现）

macOS 上**首次 exec 一个新文件**有一次性开销 ~0.15s，且该开销在并发下被全局串行化：

```
并发 1 / 2 / 5 / 10 / 20  →  max 0.204 / 0.306 / 0.751 / 1.507 / 3.006s
             （p50 恒 ~0.16s，max ≈ 并发数 × 0.15s，典型全局排队而非资源饱和）
同文件二次 exec 0.007s；读新文件 0.000s；系统 /bin/echo 0.004s
```

而本目录**每个用例都新建一个假 docker 脚本**，`-n auto` 下 10 个 worker 同时首跑 ⇒
队尾撞上 3s ⇒ 报 `docker 命令执行超时（3s）: inspect …`，且**失败集合每轮不同**。

## 为什么放宽超时不是「掩盖问题」

1. **被放宽的常量不是被测对象**：没有任何用例断言「3s 这个数本身」。断言的是
   「超时后会 kill 子进程并抛错」——那由 `test_docker_cli` 里**显式传 `timeout_sec=2`**
   的用例覆盖，与本 fixture 无关（`run()` 的 `timeout_sec` 是入参，不是读常量）。
2. **假进程没有任何「该慢」的场景**：本目录没有一条用例依赖「3s 内必须失败」；
   真要验超时行为，用例自己传小超时。
3. **反证**：放宽后仍保留 `test_docker_cli` 自己的 2s 计时窗口（那是它显式传的），
   所以「超时能收口」这条能力照样被验；只是不再被首跑开销随机打断。
4. 若担心放宽藏了真问题：**全量串行跑一轮必须全绿**，且并行 5 轮全绿
   （#124 验收标准里的两条），单靠放宽超时做不到（去掉预热后 `test_docker_cli` 仍红）。

## 为什么不改成串行 / xdist_group

实测 `@pytest.mark.xdist_group` + `--dist loadgroup` **更糟**（31 failed）：压到单 worker
后用例**逐条**新建脚本，每条都吃首跑成本，反而更容易撞窗口。串行能绿但把 128 条压成
单线程，且**掩盖根因**——下次有人加一条新用例又会冒出来。
"""
import pytest

from service.containerService import core

# 远超任何假 docker 脚本的正常耗时（实测假脚本 p99 < 0.06s），
# 但仍然有限：真挂死的假脚本会在 60s 收口，不会把测试挂到天荒地老。
RELAXED_INSPECT_TIMEOUT_SEC = 60
RELAXED_CREATE_TIMEOUT_SEC = 120


@pytest.fixture(autouse=True)
def relax_container_cli_timeouts(monkeypatch):
    """把注入 `core` 的两个 docker 超时放宽到「假 docker 不可能撞线」的量级。

    `core` 用 `from constants import ...` 拿到的是模块级名字，`core.XXX` 就是调用点读取的
    那个名字，所以 monkeypatch 到 `core` 上即可生效（不用改 `constants`，避免影响
    别的模块与真 docker 路径）。

    `autouse=True` 是刻意的：本目录每条用例都会起假 docker，逐条声明容易漏。
    """
    monkeypatch.setattr(
        core, "CONTAINER_INSPECT_TIMEOUT_SEC", RELAXED_INSPECT_TIMEOUT_SEC, raising=True
    )
    monkeypatch.setattr(
        core, "CONTAINER_CREATE_TIMEOUT_SEC", RELAXED_CREATE_TIMEOUT_SEC, raising=True
    )
