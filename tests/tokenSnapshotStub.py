"""`AgentTurnRunner._token_snapshot()` 的测试替身。

## 为什么需要它

token 口径收敛到 `AgentHistoryStore.token_snapshot()` 之后，runner 内部不再
自己调 `compact.estimate_tokens(...)`，而是统一调 `self._token_snapshot()`。
因此原来 patch `compact.estimate_tokens` 的用例必须改成 patch `_token_snapshot`：
patch 后者等于直接钉住「runner 眼里当前上下文有多长」，与具体估算实现解耦。

## 为什么不直接 patch `AgentHistoryStore.token_snapshot`

大多数既有用例把 `runner._history` 换成了 `MagicMock`，`token_snapshot()` 会返回
一个 MagicMock，`snapshot.current_tokens` 参与比较时既不会报错也不会红——
用例会「绿得没有意义」。patch `_token_snapshot()` 返回真实的
`HistoryTokenSnapshot`，至少保证快照类型和字段是真的。

## 调用次数语义

`snapshots(...)` 产出的迭代器**用尽后重复最后一个值**，因为一次推理链路里
`_token_snapshot()` 会被调用多次（pre-check / 广播 / post-check / compact 后），
而多数用例只关心其中一两个值。需要精确控制时就把值给全。
"""
from __future__ import annotations

from typing import Iterator

from service.agentService.agentHistoryStore import HistoryTokenSnapshot

#: 直接 patch 这个路径即可替换 runner 的 token 口径入口。
TOKEN_SNAPSHOT_PATCH = "service.agentService.agentTurnRunner.AgentTurnRunner._token_snapshot"


def make_snapshot(current_tokens: int, *, estimated_tokens: int | None = None) -> HistoryTokenSnapshot:
    """构造一个无真值锚点的快照（`current_tokens == estimated_tokens`）。"""
    estimated = current_tokens if estimated_tokens is None else estimated_tokens
    return HistoryTokenSnapshot(estimated_tokens=estimated, current_tokens=current_tokens)


def snapshots(*values: int | HistoryTokenSnapshot) -> Iterator[HistoryTokenSnapshot]:
    """把一串 `current_tokens` 数字变成 `side_effect` 可用的迭代器。

    int 会被包成 `HistoryTokenSnapshot`；末尾值在迭代器用尽后无限重复。
    """
    items = [
        v if isinstance(v, HistoryTokenSnapshot) else make_snapshot(v)
        for v in values
    ]

    def _gen() -> Iterator[HistoryTokenSnapshot]:
        for item in items:
            yield item
        if items:
            while True:
                yield items[-1]

    return _gen()
