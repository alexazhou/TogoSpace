# handle_finish_request：先判断停止，再推进

## 现状

```python
# handle_finish_request 当前流程
if not self.current_turn_has_content:
    self._round_skipped_set.add(current_id)
self.current_turn_has_content = False

self._go_next_turn()       # 1. 先推进 turn_pos（可能回到首位，递增 turn_count）
await self.persist_state()
if self._stop_if_done():   # 2. 再判断是否停止
    return True
next_id = self._advance_to_next_dispatchable()
...
```

**问题**：推进和判断顺序反了。应该先判断是否该停，停了就不再推进。

## 目标

```python
# 目标流程
if not self.current_turn_has_content:
    self._round_skipped_set.add(current_id)

if self._stop_if_done():   # 1. 先判断是否停止
    return True             #    turn_pos 停留在当前发言位

self._go_next_turn()       # 2. 再推进
await self.persist_state()
next_id = self._advance_to_next_dispatchable()
...
```

## 连锁问题

`_stop_if_done` 的 max_turns 条件依赖 `_turn_count`，它只在 `_go_next_turn` 回到首位时递增：

```python
def _go_next_turn(self):
    self._turn_pos = (self._turn_pos + 1) % len(self._gt_room.agent_ids)
    if self._turn_pos == 0:
        self._turn_count += 1       # 回到首位才递增
    self.current_turn_has_content = False
```

先判断再推进 → 最后一位发言人的"回到首位"还没发生 → `_turn_count` 落后一轮。

**例**（max_turns=2，agents=[a, b, c]，先判断再推进，无补偿）：

| 轮次 | c 发言完成时 count | 判断 | 结果 |
|------|--------------------|------|------|
| Round 0 | 0 | 0>=2? No | 推进，count=1 |
| Round 1 | 1 | 1>=2? No | 推进，count=2 |
| Round 2 | 2 | 2>=2? **Yes** | 停止（多跑了一轮！） |

当前代码：Round 1 完成后 count=2，停止。恰好 2 轮。
新代码：多跑 Round 2，实际跑了 3 轮。

**解**：`_turn_count` 保持"已完成轮数"语义，放在 `_go_next_turn` 中递增。`_stop_if_done` 的 max_turns 条件改为 `count == max_turns - 1` 且处于最后一位——因为本轮还没完成，count 比实际多跑一轮的值少 1。同时加上"最后一位"约束，语义更完整。

**验证**（max_turns=2，agents=[a, b, c]）：

| 轮次 | c 完成时 count | 判断 (count==1 AND last?) | 结果 |
|------|----------------|--------------------------|------|
| Round 0 | 0 | 0==1? No | 推进，count=1 |
| Round 1 | 1 | 1==1 AND last? **Yes** | 停止（恰好 2 轮） |

## 具体改动

### handle_finish_request

停止判断移到推进前：

```python
if not self.current_turn_has_content:
    self._round_skipped_set.add(current_id)

if self._stop_if_done():
    return True

self._go_next_turn()
await self.persist_state()
...
```

### on_message

新会话固定从第一位开始，重置 `_turn_pos = 0`：

```python
def on_message(self, sender_id: int) -> Optional[int]:
    if self._state in (RoomState.IDLE, RoomState.INIT):
        ...
        self._turn_count = 0
        self._round_skipped_set = set()
        self.current_turn_has_content = False
        self._turn_pos = 0
        self._state = RoomState.SCHEDULING
        if self._stop_if_done():
            return None
        result = self._advance_to_next_dispatchable()
    ...
```

### _stop_if_done

max_turns 条件：`count == max_turns - 1` 且最后一位（判断时本轮未完成，count 落后一步）。
同时 `>=` 多余，改成 `==`：

```python
def _stop_if_done(self) -> bool:
    if self._state == RoomState.IDLE:
        return True

    if (self._gt_room.max_turns > 0
        and self._turn_count == self._gt_room.max_turns - 1
        and self._turn_pos == len(self._gt_room.agent_ids) - 1):
        reason = f"已达到最大轮次 {self._gt_room.max_turns}"
    else:
        ai_ids = {aid for aid in self._gt_room.agent_ids if aid != self.OPERATOR_MEMBER_ID}
        if ai_ids and ai_ids.issubset(self._round_skipped_set):
            reason = "所有 AI 成员均已跳过发言"
        else:
            return False

    self._state = RoomState.IDLE
    logger.info("房间 %s %s，停止调度", self._key, reason)
    self.publish_status(current_turn_agent_id=None)
    return True
```

### _skip_operator_if_needed

不变（`_go_next_turn` 内部处理 `_turn_count`）：

```python
def _skip_operator_if_needed(self) -> bool:
    agent_id = self.get_current_turn_agent_id()
    if agent_id == self.OPERATOR_MEMBER_ID and self._gt_room.type == RoomType.GROUP and len(self._gt_room.agent_ids) > 2:
        self._round_skipped_set.add(agent_id)
        self._go_next_turn()
        return True
    return False
```

## 改动文件汇总

| 文件 | 改动 |
|------|------|
| `roomScheduler.handle_finish_request` | 停止判断移到推进前 |
| `roomScheduler.on_message` | 唤醒时 `_turn_pos` 直接 +1 |
| `roomScheduler._stop_if_done` | `>=` → `==`，条件改为 `count == max_turns - 1` 且最后一位 |

