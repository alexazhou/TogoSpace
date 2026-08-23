# Agent 图片视觉 Fallback（视觉模型兜底）- 开发任务表

> 状态：已实施（2026-08-24，见 Task #54）

## 任务概览

任务按依赖排序：先落提示词与配置（任务 1），再做 llmService 转换层插入（任务 2），然后做 agentService 识别（任务 3）与 agentTurnRunner 集成（任务 4），最后补测试回归（任务 5）。

> 状态标记：`[x]` 已完成，`[ ]` 待完成 / 待验证。

---

## 任务列表

### 任务 1: 提示词与开关配置

**描述**: 定义两个提示词常量与 fallback 开关，供后续任务引用。

**依赖**: 无

**核心文件**:
- `src/service/agentService/prompts.py`
- `src/service/llmService/core.py`

**子任务**:
- [x] `prompts.py` 增加 `VISION_RECOGNIZE_PROMPT`（识别提示词，默认：请描述这张图片的内容，包括文字、图表、界面元素等关键信息，供 agent 后续决策使用）
- [x] `llmService/core.py` 增加 `VISION_UNAVAILABLE_PROMPT`（无视觉说明文案：告知 agent 收到图片但模型不支持视觉、无法读取、建议配置视觉模型）
- [x] 确认 `auto_vision_fallback` 开关的配置读取方式（沿用 `CONFIG_DEFAULTS` / 设置页机制，默认 `true`）

**验收标准**:
- 两个常量可被对应模块导入，文案符合已确认口径（每张图一条说明）

### 任务 2: llmService 转换层插入消息

**描述**: `_split_tool_result_messages` 增加 `supports_vision` 参数，转换时对图片附件按能力三分支：发图 / 插入 caption 文本 / 插入说明文本。

**依赖**: 任务 1（需 `VISION_UNAVAILABLE_PROMPT`）

**核心文件**:
- `src/service/llmService/core.py`（`_split_tool_result_messages` L165、`_build_request` L211）

**子任务**:
- [x] `_split_tool_result_messages(messages, supports_vision)` 签名加 `supports_vision: bool`（默认 `True` 保持旧行为）
- [x] 转换时对每个图片附件（USER 图片消息 / TOOL 结果图拆分）三分支：
  - `supports_vision=True` → 原样生成 `OpenAIImageUrlContentBlock`（V25 流程不变）
  - `supports_vision=False` 且 `attachment.caption` 非空 → 插入 `OpenAITextContentBlock(text=caption)`，不生成 image_url
  - `supports_vision=False` 且 caption 为空 → 插入 `OpenAITextContentBlock(text=VISION_UNAVAILABLE_PROMPT)`，不生成 image_url
- [x] `_build_request` 中计算 `supports_vision = ModelInput.IMAGE in model_config.input` 并传入
- [x] 保持 `to_openai_message`（agentMessage.py L101）与 `compact.py`（L83）签名不变

**验收标准**:
- 支持视觉模型：图片正常发送（V25 回归）
- 不支持视觉 + 有 caption：请求体只有文本，无 `image_url` content block
- 不支持视觉 + 无 caption：请求体插入说明文本，无图片
- `infer` / `infer_stream` 均生效

### 任务 3: agentService 识别（visionFallback）

**描述**: 新增 `visionFallback.py`，封装 `recognize(att)`：接收一张图片附件，用视觉模型识别并返回纯文本描述（循环写 caption 与持久化由 agentTurnRunner 负责）。

**依赖**: 任务 1（需 `VISION_RECOGNIZE_PROMPT`）

**核心文件**:
- `src/service/agentService/visionFallback.py`（新增）

**子任务**:
- [x] `recognize(att: MessageAttachment) -> str`：接收单张图片附件，返回识别文本（输入仅 `VISION_RECOGNIZE_PROMPT` + 图片，不携带完整历史）；识别结果写入 `MessageAttachment.recognition`（新字段，与 caption 引导语区分，保留 caption 内容）
- [x] 识别结果截断约 500 字
- [x] 识别失败（超时 / 报错 / `vision@system` 未配置抛 ValueError）→ 抛异常，由调用方捕获降级，不阻塞主流程

**验收标准**:
- 无 caption 的图片被识别并写回 caption，DB 持久化成功
- 已有 caption 的图片跳过识别（不重复调用视觉模型）
- 识别失败不抛异常、不影响主流程

### 任务 4: agentTurnRunner 集成

**描述**: `_infer_to_item` 中接入识别：在 `build_infer_messages()` 之后、构造 ctx 之前，按能力判断决定是否调用 `_vision_fallback_recognize`（遍历无 caption 图片逐张调用 `visionFallback.recognize` 写 caption 并显式持久化）；overflow retry 路径同样覆盖。

**依赖**: 任务 2、3

**核心文件**:
- `src/service/agentService/agentTurnRunner.py`（`_infer_to_item` L427）

**子任务**:
- [x] `_infer_to_item` 内 `resolve_model(self.gt_agent.model)`（L440 已有）后计算 `supports_vision` 与 `vision_configured`
- [x] 场景 B（不支持视觉 + vision 已配 + 开关开）：`build_infer_messages()`（L459）后调用 `_vision_fallback_recognize`：对无 recognition 的图片逐张 `visionFallback.recognize(att)` 写 recognition，并按 history id 调 `update_agent_history_by_id` 显式持久化（工具结果引导语 caption 不阻止识别）
- [x] 正常路径（L478）与 overflow retry 路径（L547 重新 build、L570 重建 ctx）各自直接构造 `GtCoreAgentDialogContext`，不封装辅助函数（Operator 确认）

**验收标准**:
- 正常 turn：主模型不支持视觉 + 有图 → 识别写 caption 后再推理
- overflow retry 路径同样触发识别/转换，不遗漏
- 主模型支持视觉：不调用识别，走 V25 流程

### 任务 5: 测试与回归

**描述**: 覆盖三种场景的自动化测试。

**依赖**: 任务 2、3、4

**核心文件**:
- `src/service/agentService/visionFallback.py` 测试（新增）
- `src/service/llmService/core.py` 相关测试
- `src/service/agentService/agentTurnRunner.py` 相关测试

**子任务**:
- [x] 转换层单测：`_split_tool_result_messages(messages, supports_vision)` 三分支（发图 / caption 文本 / 说明文本），断言不支持视觉时请求体无 `image_url`
- [x] 识别单测：无 caption 图片被识别并写 caption；有 caption 图片跳过；识别失败降级不抛
- [x] 持久化单测：`recognize` 后 DB 中 history message 的 caption 已更新
- [x] 集成测试：agentTurnRunner 场景 B 走识别 + 转换插入；场景 A 回归 V25
- [x] 全量单测通过（沿用 V25 的 pytest 体系）

**验收标准**:
- 三场景（支持视觉 / fallback / 提示）自动化测试全绿
- 现有 V25 多模态用例不回归

---

## 依赖关系

```
任务 1 ──┬──> 任务 2
         └──> 任务 3 ──> 任务 4
任务 5 <── 任务 2、3、4
```
