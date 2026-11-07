"""泛型缓存工具，支持任意类型的对象缓存。"""
from __future__ import annotations

from typing import Generic, TypeVar, Callable, Iterable

K = TypeVar("K")
V = TypeVar("V")


class CacheStore(Generic[K, V]):
    """泛型缓存存储，支持单对象、批量操作和按版本号回填。

    使用示例：
        # 简单缓存（key 为 int，value 为任意对象）
        agent_cache = CacheStore[int, GtAgent]()
        agent_cache.set(agent.id, agent)
        agent = agent_cache.get(agent_id)

        # 使用 key_extractor 自动提取 key
        agent_cache = CacheStore[int, GtAgent](key_extractor=lambda a: a.id)
        agent_cache.add(agent)  # 自动用 agent.id 作为 key
        agent_cache.add_many([agent1, agent2])

        # 读穿透 + 版本号检查：查询数据库前取版本号，回填时交回
        version = agent_cache.version()
        agent = await load_from_db(agent_id)      # 期间可能被 invalidate
        agent_cache.add_if_version(agent, version)  # 已被失效则丢弃
    """

    def __init__(self, key_extractor: Callable[[V], K] | None = None) -> None:
        """初始化缓存存储。

        Args:
            key_extractor: 可选的 key 提取函数，用于 add/add_many 方法自动提取 key。
                           若不提供，则必须显式调用 set/set_many 并传入 key。
        """
        self._store: dict[K, V] = {}
        self._key_extractor: Callable[[V], K] | None = key_extractor
        # 版本号：invalidate/clear 递增；查询前取快照，回填时用于判断是否已失效
        self._version: int = 0
        self._key_invalidated_at: dict[K, int] = {}
        self._cleared_at: int = 0

    def version(self) -> int:
        """返回当前缓存版本号（单调递增）。

        查询数据库前取一次，回填时交回 `add_if_version` / `add_many_if_version`，
        即可判断查询期间是否发生过失效。
        """
        return self._version

    def _is_fresh(self, key: K, version: int) -> bool:
        """判断 key 自 version 之后是否未被失效过。"""
        return (
            self._cleared_at <= version
            and self._key_invalidated_at.get(key, 0) <= version
        )

    def set(self, key: K, value: V) -> None:
        """设置单个缓存项（直接写入，不做版本检查）。"""
        self._store[key] = value

    def get(self, key: K) -> V | None:
        """获取单个缓存项，不存在时返回 None。"""
        return self._store.get(key)

    def contains(self, key: K) -> bool:
        """检查 key 是否在缓存中。"""
        return key in self._store

    def invalidate(self, key: K) -> None:
        """失效单个缓存项，并推进版本号（只影响该 key 的在途回填）。"""
        self._store.pop(key, None)
        self._version += 1
        self._key_invalidated_at[key] = self._version

    def clear(self) -> None:
        """清空所有缓存，并推进版本号（使所有在途回填失效）。"""
        self._store.clear()
        self._key_invalidated_at.clear()
        self._version += 1
        self._cleared_at = self._version

    def set_many(self, items: dict[K, V]) -> None:
        """批量设置缓存项（直接写入，不做版本检查）。"""
        self._store.update(items)

    def get_many(self, keys: Iterable[K]) -> dict[K, V]:
        """批量获取缓存项，返回存在的项。"""
        return {k: self._store[k] for k in keys if k in self._store}

    def add(self, value: V) -> None:
        """添加单个对象到缓存（使用 key_extractor 提取 key）。

        Raises:
            ValueError: 若未配置 key_extractor。
        """
        if self._key_extractor is None:
            raise ValueError("key_extractor is required for add() method")
        key = self._key_extractor(value)
        self._store[key] = value

    def add_many(self, values: Iterable[V]) -> None:
        """批量添加对象到缓存（使用 key_extractor 提取 key）。

        Raises:
            ValueError: 若未配置 key_extractor。
        """
        if self._key_extractor is None:
            raise ValueError("key_extractor is required for add_many() method")
        for value in values:
            key = self._key_extractor(value)
            self._store[key] = value

    def add_if_version(self, value: V, version: int) -> bool:
        """带版本号检查的单对象写入：自 version 以来该 key 未失效才写入。

        Returns:
            是否写入缓存。
        Raises:
            ValueError: 若未配置 key_extractor。
        """
        if self._key_extractor is None:
            raise ValueError("key_extractor is required for add_if_version() method")
        key: K = self._key_extractor(value)
        if not self._is_fresh(key, version):
            return False
        self._store[key] = value
        return True

    def add_many_if_version(self, values: Iterable[V], version: int) -> None:
        """带版本号检查的批量写入：逐项判断，跳过查询期间被失效过的 key。

        Raises:
            ValueError: 若未配置 key_extractor。
        """
        if self._key_extractor is None:
            raise ValueError("key_extractor is required for add_many_if_version() method")
        for value in values:
            key: K = self._key_extractor(value)
            if self._is_fresh(key, version):
                self._store[key] = value

    def size(self) -> int:
        """返回缓存项数量。"""
        return len(self._store)