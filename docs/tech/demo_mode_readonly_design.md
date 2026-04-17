# 演示模式只读方案设计

本文档定义 TogoAgent 的“演示模式（Demo Mode）”技术方案。

目标场景是搭建公开或半公开的演示站点，让用户可以浏览团队、房间、消息、活动等界面效果，但不允许任何数据写入，也尽量不暴露敏感配置。

---

## 1. 背景

当前系统默认以“可运行、可写入”的产品形态工作：

- 前端页面可以发送消息、修改团队、编辑房间、调整模型服务配置
- 后端启动时会导入 preset、恢复 Team runtime、启动调度
- 调度链路会创建 task、追加消息、更新活动、推进房间状态
- 设置与目录接口会返回部分敏感信息，如 API Key、本地目录路径、工作目录等

这意味着，如果直接把现有服务作为演示站暴露出去，会有以下问题：

- 外部用户可以直接改动数据
- 即使前端禁用按钮，后端启动恢复和调度流程仍可能继续写库
- 公开站点可能暴露 API Key、内网地址、本地目录路径等不适合外显的信息

因此需要一个“系统级”的演示模式，而不是仅靠前端隐藏按钮。

---

## 2. 目标与非目标

### 2.1 目标

演示模式需要满足以下目标：

1. 用户可以正常浏览已有数据
2. 任何会造成数据变化的操作都被禁止
3. 后端在演示模式下不会因为启动恢复或调度继续写入数据库
4. 前端能明确提示“当前是演示模式”
5. 对外暴露时尽量隐藏敏感配置和本地路径
6. 方案应尽量复用现有代码结构，避免大规模重构

### 2.2 非目标

本方案首版不追求以下能力：

- 不实现“可交互但不落库”的伪写入体验
- 不实现多租户级别的精细权限控制
- 不把“只读能力”设计成通用 ACL 系统
- 不要求首版就实现 SQLite 底层强只读连接

首版只关注“可浏览、不可写”的公共演示站能力。

---

## 3. 术语与配置语义

### 3.1 配置结构

建议在 `SettingConfig` 中新增：

```json
{
  "demo_mode": {
    "enabled": true,
    "freeze_data": true,
    "hide_sensitive_info": true
  }
}
```

字段语义如下：

| 字段 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `enabled` | `bool` | `false` | 是否启用演示模式 |
| `freeze_data` | `bool` | `true` | 是否冻结数据变更；开启后系统进入只读浏览态 |
| `hide_sensitive_info` | `bool` | `true` | 是否隐藏 API Key、本地目录、工作目录等敏感信息 |

建议模型定义：

```python
class DemoModeConfig(BaseModel):
    enabled: bool = False
    freeze_data: bool = True
    hide_sensitive_info: bool = True
```

并挂到 `SettingConfig`：

```python
demo_mode: DemoModeConfig = Field(default_factory=DemoModeConfig)
```

---

## 4. 当前基线与问题定位

当前代码里，写入并不只发生在“用户点了保存”这种显式交互中，还会发生在系统自身的启动与调度流程中。

### 4.1 用户触发的写入入口

当前主要写入口集中在以下 controller：

- `src/controller/settingController.py`
- `src/controller/initController.py`
- `src/controller/teamController.py`
- `src/controller/roomController.py`
- `src/controller/agentController.py`
- `src/controller/roleTemplateController.py`
- `src/controller/deptController.py`

其中包含：

- 模型服务创建、修改、删除、设默认、测试
- Quick Init 保存配置
- Team / Room / Agent / DeptTree 的增删改
- 发送消息
- Agent stop / resume
- 清空团队运行数据

### 4.2 系统自身触发的写入

后端启动和调度过程中也会产生写入：

1. `src/backend_main.py`
   - 启动时会执行 `presetService.import_from_app_config()`
   - 启动时会对 enabled team 执行 `teamService.restore_team(...)`
   - 启动后会执行 `schedulerService.start_schedule()`

2. `src/service/presetService.py`
   - 会把 role template / team preset 导入数据库

3. `src/service/agentService/core.py`
   - 恢复过程中会把遗留 RUNNING task 标记为 FAILED

4. `src/service/schedulerService.py`
   - 调度会创建 task、触发 consumer、推进消息与活动

因此，如果只在前端禁用按钮：

- 不能阻止后端 API 被直接调用
- 不能阻止启动恢复和调度继续写入
- 不能保证数据真正冻结

---

## 5. 核心设计

核心思路分为 4 层：

1. 配置层：通过 `demo_mode` 明确表达运行模式
2. Controller 层：统一拦截所有写请求
3. 启动与调度层：禁止系统自身产生写入
4. 前端层：展示模式状态并禁用交互

在首版中，`enabled=true && freeze_data=true` 表示“演示只读模式”。

---

## 6. 行为矩阵

建议系统行为按下表定义：

| 场景 | 普通模式 | `enabled=true, freeze_data=true` |
|------|----------|----------------------------------|
| 页面浏览 | 允许 | 允许 |
| 消息发送 | 允许 | 禁止 |
| Team / Room / Agent 编辑 | 允许 | 禁止 |
| LLM 服务配置修改 | 允许 | 禁止 |
| Quick Init | 允许 | 禁止 |
| Agent stop / resume | 允许 | 禁止 |
| 启动恢复写库 | 正常执行 | 跳过 |
| preset 导入 | 正常执行 | 跳过 |
| 调度器创建任务 | 正常执行 | 禁止 |
| 敏感信息显示 | 正常 | 隐藏或脱敏 |

说明：

- 首版不开放 `enabled=true && freeze_data=false` 的中间态
- 该状态可作为后续扩展预留，但当前不纳入实现范围

---

## 7. 后端方案

### 7.1 配置层改造

修改文件：

- `src/util/configTypes.py`

新增 `DemoModeConfig` 并挂入 `SettingConfig`。

同时建议在 `assets/config_template.json` 中补充默认配置示例：

```json
{
  "demo_mode": {
    "enabled": false,
    "freeze_data": true,
    "hide_sensitive_info": true
  }
}
```

这样演示模式成为正式配置能力，而不是临时环境变量分支。

### 7.2 Controller 统一只读闸门

修改文件：

- `src/controller/baseController.py`

在 `BaseHandler.prepare()` 中增加统一写入拦截。

建议规则：

- `GET`
- `HEAD`
- `OPTIONS`

以上方法直接放行。

- `POST`
- `PUT`
- `PATCH`
- `DELETE`

在 `demo_mode.enabled && demo_mode.freeze_data` 时统一返回 `403`。

建议错误返回：

```json
{
  "error_code": "demo_mode_data_frozen",
  "error_desc": "演示模式已冻结数据，当前操作不可用"
}
```

设计原则：

- 只读闸门应放在基类，避免分散到每个 controller
- 只读闸门是安全边界，前端禁用只是体验增强
- 新增写接口时，无需额外记得再补演示模式判断

### 7.3 启动阶段禁止系统写入

修改文件：

- `src/backend_main.py`

当 `demo_mode.enabled && demo_mode.freeze_data` 时，启动流程调整如下：

1. 保留基础 service 启动
2. 跳过 `presetService.import_from_app_config()`
3. 保留对 enabled team 的 `teamService.restore_team(...)`
4. 但恢复链路内部需要跳过会写库的恢复动作
5. 不调用 `schedulerService.start_schedule()`
6. 显式调用 `schedulerService.stop_schedule("演示模式已冻结数据")`

这样可以避免：

- 启动时导入 preset 写库
- 恢复 runtime 时修改 task 状态
- 启动后自动进入调度链路

说明：

- 首版只定义“冻结数据”的对外语义
- 具体实现应以保证系统进入只读浏览态为准

### 7.4 调度层状态约束

修改文件：

- `src/service/schedulerService.py`

虽然在 `backend_main.py` 中已经可以避免启动调度，但调度层仍建议保留一层保护：

- `start_schedule()` 中若处于演示只读模式，则不进入 `RUNNING`
- 状态改为 `BLOCKED` 或 `STOPPED`
- `not_running_reason` 统一设置为“演示模式已冻结数据”

这样即使未来某个调用点意外触发 `start_schedule()`，也不会真的恢复任务调度。

### 7.5 系统状态与前端配置返回 demo 标识

修改文件：

- `src/controller/configController.py`
- `src/controller/systemController.py`

建议增加以下返回字段：

```json
{
  "demo_mode": true,
  "freeze_data": true,
  "read_only": true,
  "hide_sensitive_info": true
}
```

字段说明：

- `demo_mode`
  表示当前是否在演示模式
- `freeze_data`
  表示当前是否冻结数据
- `read_only`
  供前端直接消费的 UI 只读标记
- `hide_sensitive_info`
  供前端决定是否隐藏敏感区域

建议保留 `freeze_data` 和 `read_only` 两个字段：

- `freeze_data` 对应配置语义
- `read_only` 对应 UI 消费语义

### 7.6 敏感信息脱敏

修改文件建议：

- `src/controller/settingController.py`
- `src/controller/configController.py`
- `src/controller/teamController.py`

当 `hide_sensitive_info=true` 时，建议做以下处理：

#### 7.6.1 LLM 服务配置

在 `LlmServiceListHandler` 中：

- 不返回真实 `api_key`
- 可改为 `has_api_key: true`
- 视需要对 `base_url` 进行掩码或完全隐藏

#### 7.6.2 系统目录

在 `DirectoriesHandler` 中：

- 不返回真实 `config_dir`
- 不返回真实 `workspace_dir`
- 不返回真实 `data_dir`
- 不返回真实 `log_dir`

可选方案：

- 返回空字符串
- 返回通用占位文案
- 或直接在 demo 模式下隐藏该接口入口

#### 7.6.3 Team 详情

在 `TeamDetailHandler` 中：

- `working_directory` 置空
- `config` 仅保留白名单字段，或直接返回空对象

---

## 8. 前端方案

前端目标不是做安全控制，而是：

- 显示当前处于演示模式
- 尽量避免用户点击到不可用操作
- 对只读行为给出清晰反馈

### 8.1 模式状态接入

建议修改文件：

- `frontend/src/api.ts`
- `frontend/src/types.ts`
- `frontend/src/App.vue`

前端从以下接口读取模式状态：

- `/system/status.json`
- `/config/frontend.json`

并在全局状态中保存：

- `demo_mode`
- `freeze_data`
- `read_only`
- `hide_sensitive_info`

### 8.2 全局视觉提示

建议在页面顶部增加“演示模式”标识，例如：

- TopBar badge
- 全局横幅
- 状态栏提示

提示语建议简单明确：

- “演示模式”
- “当前站点为只读浏览模式”

### 8.3 交互禁用范围

在 `read_only=true` 时，建议前端统一禁用以下操作：

- Console 消息输入框与发送按钮
- Team / Room / Agent 编辑按钮
- DeptTree 保存按钮
- 模型服务的创建、保存、删除、测试、设默认按钮
- Quick Init 弹窗入口
- Agent stop / resume 按钮
- 清空数据按钮

如果产品上允许更保守的体验，也可以：

- 直接隐藏 Settings 入口
- 或仅保留浏览型设置页，不展示可写配置区

### 8.4 错误与提示策略

当前端用户点击了只读状态下的按钮时，建议：

- 优先在 UI 层禁用按钮
- 如果仍触发请求，由后端返回统一错误
- 前端 toast 展示后端错误文案即可

不建议前端自己构造多套文案，避免和后端语义不一致。

---

## 9. 数据与部署方案

### 9.1 独立演示数据

演示站必须使用独立数据，不应直接复用真实运行库。

建议：

- 使用独立 `--config-dir`
- 使用独立 `setting.json`
- 使用独立 `db_path`
- 使用独立 `workspace_root`
- 使用独立日志目录

原因：

- 真库里可能包含不适合公开的消息、团队、路径、配置
- 即使应用层只读，也不代表数据适合对外展示
- 演示数据应当稳定、可回滚、可重复部署

### 9.2 演示快照发布流程

建议的发布流程：

1. 在内部环境准备好一套可展示的演示数据
2. 导出或复制出专用的 demo SQLite 数据库
3. 准备专用 `setting.json`，开启 `demo_mode`
4. 使用独立 `--config-dir` 启动演示站
5. 演示站只提供浏览，不参与业务写入

### 9.3 数据清洗建议

发布前建议对 demo 数据做一次稳定化处理：

- 清理不想公开的 Team / Room / Message
- 把遗留 RUNNING task 处理为终态
- 避免房间停留在“正在执行但永远不结束”的中间态
- 清除调试日志、临时报错、测试消息
- 检查工作目录、路径、prompt 内容是否适合外显

---

## 10. 实施范围

### 10.1 V1 最小落地范围

V1 建议只实现以下内容：

1. 配置中新增 `demo_mode`
2. 后端统一写入拦截
3. 启动时跳过 preset 导入、runtime 恢复、调度启动
4. 配置接口与状态接口返回 `read_only` / `freeze_data`
5. 前端显示“演示模式”并禁用主要写操作
6. 脱敏 LLM 配置与目录路径
7. 使用独立 demo 数据部署

这已经足够支撑“可浏览、不可写”的演示站。

### 10.2 后续可扩展范围

后续如果要增强，可以继续演进：

1. SQLite 连接层增加强只读模式
2. 支持“允许交互，但不持久化”的高级演示模式
3. 支持“导览模式”或内置演示团队入口
4. 增加演示数据重置脚本
5. 允许按页面粒度决定哪些区域可见

---

## 11. 风险与权衡

### 11.1 为什么首版不做底层数据库只读

从长期看，SQLite 只读连接是更强的保险。

但当前 `ormService` 启动逻辑会：

- 自动建目录
- 执行 migration
- 使用普通可写连接

如果首版就切到底层只读，需要同时改造：

- ORM 启动逻辑
- migration 时机
- 数据准备流程

这会明显增加改造范围。

因此首版建议先做“应用层严格只读 + 启动链路停写”，后续再补底层只读。

### 11.2 为什么首版不做“假写入”

“假写入”是指前端仍允许操作，但结果只存在于内存，不落库。

当前系统里，消息、任务、活动、房间状态、调度状态彼此联动较深。首版如果引入“假写入”，会带来：

- 刷新后状态回滚
- UI 与后端状态不一致
- 需要额外处理内存态与持久态分叉

因此首版更适合明确只读，而不是伪交互。

---

## 12. 验收标准

实现完成后，建议按以下标准验收：

1. 演示站可正常浏览 Team / Room / Message / Activity
2. 所有写请求均返回统一的只读错误
3. 前端主要修改入口均被禁用或隐藏
4. 页面顶部能明确看到“演示模式”标识
5. 后端启动后不会因恢复和调度写入新数据
6. 不会弹出 Quick Init
7. 不会暴露真实 API Key、本地目录路径、工作目录
8. 刷新页面后数据稳定不变

---

## 13. 建议实施顺序

建议按以下顺序推进，风险最低：

1. `src/util/configTypes.py`
   - 新增 `DemoModeConfig`
   - 挂到 `SettingConfig`

2. `src/controller/baseController.py`
   - 增加统一只读闸门

3. `src/backend_main.py`
   - 在演示模式下跳过 preset 导入、runtime 恢复、调度启动

4. `src/controller/configController.py`
   - 返回 demo 标识

5. `src/controller/systemController.py`
   - 返回 demo 标识与只读状态

6. `src/controller/settingController.py`
   - 脱敏 LLM 配置

7. `frontend/src/api.ts`
   - 接入模式字段

8. `frontend/src/types.ts`
   - 增加模式状态类型

9. `frontend/src/App.vue`
   - 显示演示模式标识
   - 全局禁用主要写操作入口

10. 其余前端写操作页面
   - Console / Settings / TeamEditor / QuickInit 逐步接入只读态

---

## 14. 结论

演示模式的本质不是“前端禁用按钮”，而是“系统进入只读浏览态”。

在当前项目结构下，最稳妥的首版方案是：

- 用 `demo_mode.enabled + freeze_data` 明确表达模式
- 在 Controller 基类统一拦截写请求
- 在启动阶段跳过所有可能写库的恢复和调度行为
- 通过前端展示状态并禁用交互
- 使用独立 demo 数据，并对敏感信息做脱敏处理

该方案改造范围可控，能较好贴合现有代码结构，并为后续更强的底层只读和高级演示模式预留扩展空间。
