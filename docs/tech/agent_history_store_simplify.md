# AgentHistoryStore 简化计划

> 第一性原理：`AgentHistoryStore` 的核心职责是**历史消息的读写与持久化，以及为 LLM 提供正确的消息窗口**。其余逻辑应迁出或删除。

---

## 待处理项

### ✅ 已完成（本次重构前置工作）
- 删除 `RuntimeWindow` / `_build_runtime_window`
- 引入 compact 不变量（COMPACT_SUMMARY 必在 `_items[0]`），简化 `_get_window_items`
- 合并 `find_tool_call_by_id` 两个变体
- 删除 `get_runtime_window_start_index`、`_find_compact_preserve_start_index`
- 方法重命名（去掉 `_in_unfinished_turn` 等冗长后缀）
- 删除 `has_pending_tool_calls()`（从未被调用）
- 内联 `get_last_turn_assistant_message()` 到 `get_first_pending_tool_call`，删除该方法
- 删除 `export_openai_message_list()`，测试改用 `[item.openai_message for item in history]`
- 删除 `dump()`，调用方改用 `list(history)`
- `assert_infer_ready` 改为 `is_infer_ready() -> bool`，assert 逻辑移至 runner；`last_role` 私有化为 `_last_role`
- `next_seq` 私有化为 `_next_seq`
- `_infer_role_from_stage` 迁移至 `GtAgentHistory.infer_role_from_stage`，与 `infer_stage_from_message` 成对

---

### 1. 删除 `has_active_turn()`

**问题**：纯 boolean wrapper，一行代码。

```python
def has_active_turn(self) -> bool:
    return self.get_current_turn_start_index() is not None
```

**方案**：删除，调用方改为直接写 `is not None` 判断。

---

## 改完后预期保留的核心接口

| 写操作 | 读操作 |
|--------|--------|
| `append_history_message` | `build_infer_messages` |
| `append_history_init_item` | `build_compact_plan` |
| `finalize_history_item` | `get_pending_infer_item` |
| `insert_compact_summary` | `get_current_turn_start_index` |
| | `get_first_pending_tool_call` |
| | `find_tool_call_by_id` |
| | `find_tool_result_by_call_id` |
| | `is_infer_ready` |

以及必要的 list 协议：`__len__`、`__iter__`、`__getitem__`、`replace`、`last`。
