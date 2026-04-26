"""CacheStore 泛型缓存类的单元测试。"""
from dataclasses import dataclass

import pytest

from util.cacheUtil import CacheStore


@dataclass
class _TestItem:
    """测试用的简单数据类。"""
    id: int
    name: str


class TestCacheStoreBasic:
    """基本操作测试（不使用 key_extractor）。"""

    def test_set_and_get(self):
        cache = CacheStore[int, str]()
        cache.set(1, "alice")
        assert cache.get(1) == "alice"

    def test_get_missing_returns_none(self):
        cache = CacheStore[int, str]()
        assert cache.get(999) is None

    def test_contains(self):
        cache = CacheStore[int, str]()
        cache.set(1, "alice")
        assert cache.contains(1) is True
        assert cache.contains(2) is False

    def test_invalidate(self):
        cache = CacheStore[int, str]()
        cache.set(1, "alice")
        cache.invalidate(1)
        assert cache.get(1) is None

    def test_invalidate_missing_key_is_safe(self):
        cache = CacheStore[int, str]()
        cache.invalidate(999)  # 不存在，不应报错
        assert cache.size() == 0

    def test_clear(self):
        cache = CacheStore[int, str]()
        cache.set(1, "alice")
        cache.set(2, "bob")
        cache.clear()
        assert cache.size() == 0
        assert cache.get(1) is None

    def test_set_many(self):
        cache = CacheStore[int, str]()
        cache.set_many({1: "alice", 2: "bob"})
        assert cache.get(1) == "alice"
        assert cache.get(2) == "bob"

    def test_get_many(self):
        cache = CacheStore[int, str]()
        cache.set_many({1: "alice", 2: "bob", 3: "charlie"})
        result = cache.get_many([1, 3])
        assert result == {1: "alice", 3: "charlie"}

    def test_get_many_with_missing_keys(self):
        cache = CacheStore[int, str]()
        cache.set(1, "alice")
        result = cache.get_many([1, 999])  # 999 不存在
        assert result == {1: "alice"}

    def test_size(self):
        cache = CacheStore[int, str]()
        assert cache.size() == 0
        cache.set(1, "alice")
        assert cache.size() == 1
        cache.set_many({2: "bob", 3: "charlie"})
        assert cache.size() == 3


class TestCacheStoreWithKeyExtractor:
    """使用 key_extractor 的测试。"""

    def test_add_single(self):
        cache = CacheStore[int, _TestItem](key_extractor=lambda i: i.id)
        item = _TestItem(id=1, name="alice")
        cache.add(item)
        assert cache.get(1).name == "alice"

    def test_add_many(self):
        cache = CacheStore[int, _TestItem](key_extractor=lambda i: i.id)
        items = [_TestItem(id=1, name="alice"), _TestItem(id=2, name="bob")]
        cache.add_many(items)
        assert cache.size() == 2
        assert cache.get(1).name == "alice"
        assert cache.get(2).name == "bob"

    def test_add_without_extractor_raises(self):
        cache = CacheStore[int, str]()  # 无 key_extractor
        with pytest.raises(ValueError, match="key_extractor is required"):
            cache.add("alice")

    def test_add_many_without_extractor_raises(self):
        cache = CacheStore[int, str]()  # 无 key_extractor
        with pytest.raises(ValueError, match="key_extractor is required"):
            cache.add_many(["alice", "bob"])


class TestCacheStoreOverwrite:
    """覆盖更新测试。"""

    def test_set_overwrites_existing(self):
        cache = CacheStore[int, str]()
        cache.set(1, "alice")
        cache.set(1, "bob")  # 覆盖
        assert cache.get(1) == "bob"

    def test_add_overwrites_existing(self):
        cache = CacheStore[int, _TestItem](key_extractor=lambda i: i.id)
        cache.add(_TestItem(id=1, name="alice"))
        cache.add(_TestItem(id=1, name="bob"))  # 覆盖
        assert cache.get(1).name == "bob"