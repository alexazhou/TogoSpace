"""泛型缓存工具，支持任意类型的对象缓存。"""
from __future__ import annotations

from typing import Generic, TypeVar, Callable, Iterable

K = TypeVar("K")
V = TypeVar("V")


class CacheStore(Generic[K, V]):
    """泛型缓存存储，支持单对象和批量操作。

    使用示例：
        # 简单缓存（key 为 int，value 为任意对象）
        agent_cache = CacheStore[int, GtAgent]()
        agent_cache.set(agent.id, agent)
        agent = agent_cache.get(agent_id)

        # 使用 key_extractor 自动提取 key
        agent_cache = CacheStore[int, GtAgent](key_extractor=lambda a: a.id)
        agent_cache.add(agent)  # 自动用 agent.id 作为 key
        agent_cache.add_many([agent1, agent2])
    """

    def __init__(self, key_extractor: Callable[[V], K] | None = None) -> None:
        """初始化缓存存储。

        Args:
            key_extractor: 可选的 key 提取函数，用于 add/add_many 方法自动提取 key。
                           若不提供，则必须显式调用 set/set_many 并传入 key。
        """
        self._store: dict[K, V] = {}
        self._key_extractor = key_extractor

    def set(self, key: K, value: V) -> None:
        """设置单个缓存项。"""
        self._store[key] = value

    def get(self, key: K) -> V | None:
        """获取单个缓存项，不存在时返回 None。"""
        return self._store.get(key)

    def contains(self, key: K) -> bool:
        """检查 key 是否在缓存中。"""
        return key in self._store

    def invalidate(self, key: K) -> None:
        """失效单个缓存项。"""
        self._store.pop(key, None)

    def clear(self) -> None:
        """清空所有缓存。"""
        self._store.clear()

    def set_many(self, items: dict[K, V]) -> None:
        """批量设置缓存项。"""
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

    def size(self) -> int:
        """返回缓存项数量。"""
        return len(self._store)