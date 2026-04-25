# TogoSpace 改名方案

## 背景

项目当前对外名称为 `TogoAgent`。本次计划将对外品牌名称统一调整为 `TogoSpace`，以更准确表达“多 Agent 共处的协作空间”这一产品形态。

除品牌名称调整外，本次还增加一项内部符号收敛：

- 将业务异常类 `TeamAgentException` 重命名为 `TogoException`
- 将文档中遗留的 `TogoAgentException` 表述一并统一为 `TogoException`

本方案关注仓库内源码、前端子模块、构建发布链路、运行时兼容和文档同步，不包含 GitHub 仓库重命名、域名切换和对外公告执行细节。

## 目标

1. 对用户可见的产品名称统一为 `TogoSpace`
2. 对打包产物、发布标题、Docker 名称等外部标识统一为 `TogoSpace`
3. 内部业务异常名称统一收敛为 `TogoException`
4. 在不破坏老用户现有配置和数据的前提下完成运行时目录迁移

## 非目标

- 不重命名与业务模型强绑定的 `Agent` 概念，例如 `agentService`、`Agent` 数据结构、`get_togo_agents()` 等
- 不强制重构环境变量前缀，例如 `TEAMAGENT_PRESET_DIR`
- 不处理历史版本文档中的所有叙述性内容，只修正会误导当前实现的关键表述
- 不自动执行 git 提交、推送、仓库或 Docker Hub 仓库改名

## 改名原则

### 原则一：优先改用户可见名称

托盘标题、页面标题、README、发布标题、安装包名称应优先统一，确保用户第一眼看到的新名称一致。

### 原则二：谨慎处理运行时路径

`~/.togo_agent` 已经承载用户配置、数据库、日志和 workspace。若直接改为 `~/.togospace` 且不做兼容，老用户升级后会出现“配置丢失”或“数据为空”的体验问题。

### 原则三：避免无收益的大面积内部重命名

本次仅新增一项内部重命名：`TeamAgentException -> TogoException`。其余内部 `Agent` 相关命名保留，避免引入大量噪音改动和额外回归风险。

## 影响范围

### 1. 用户可见名称

- `src/appEntry.py`
- `src/trayMenu.py`
- `frontend/index.html`
- `README.md`
- `README_CN.md`

说明：

- 托盘名称、窗口标题、Web 页签标题、README 文案应统一改为 `TogoSpace`

### 2. 打包与发布产物

- `scripts/togo_agent.spec`
- `scripts/build_mac.py`
- `scripts/build_release.py`
- `.github/workflows/release.yml`

说明：

- `.app` 名称从 `TogoAgent.app` 改为 `TogoSpace.app`
- 带版本号的产物名从 `TogoAgent-<version>.app/.zip` 改为 `TogoSpace-<version>.app/.zip`
- Release 标题同步更新

### 3. Docker 与发布链路

- `Dockerfile`
- `docker-compose.yml`
- `.github/workflows/docker.yml`
- `docs/DOCKER.md`

说明：

- Docker image、container、volume、文档示例中的旧名称需要统一
- 是否同步切换 Docker Hub 仓库名，需要与实际仓库准备情况一致

### 4. 运行时目录与兼容

- `src/appPaths.py`
- `assets/setting.README.md`
- `tests/**/config/setting.README.md`
- 相关技术文档

说明：

- 当前 frozen 模式默认目录为 `~/.togo_agent`
- 本次建议引入新目录 `~/.togospace`，但保留旧目录兼容

### 5. 内部异常类收敛

- `src/exception.py`
- `src/controller/baseController.py`
- `src/util/assertUtil.py`
- `src/service/presetService.py`
- `src/service/deptService.py`
- `src/service/teamService.py`
- `src/service/roomService.py`
- `src/service/agentService/core.py`
- `docs/mvc/controller_development.md`
- `docs/versions/v14/v14_step2_technical.md`

说明：

- 代码中实际使用的是 `TeamAgentException`
- 文档中遗留了 `TogoAgentException` 表述
- 本次统一目标名称为 `TogoException`

### 6. 其他文档与仓库元数据

- `.gitmodules`
- `docs/RELEASE_HANDBOOK.md`
- `docs/tech/i18n_design.md`
- `docs/tech/demo_mode_readonly_design.md`
- `docs/tech/test_execution_architecture.md`
- `docs/tech/test_case_design_guide.md`
- `hacker_news_post.txt`
- `v2ex_post.txt`

说明：

- 这些内容不一定影响运行，但会影响一致性和后续维护

## 关键决策

### 决策一：运行时目录采用“新目录优先，旧目录兼容”

建议实现：

1. frozen 模式优先使用 `~/.togospace`
2. 如果 `~/.togospace` 不存在，但 `~/.togo_agent` 存在，则自动回退到旧目录
3. 可选：在明确条件下提供一次性迁移逻辑，将旧目录内容迁移到新目录

推荐先落地第 1、2 步，暂不在首次改名版本中引入自动搬迁，以降低风险。

### 决策二：异常类直接重命名，不保留长期双名

建议实现：

- 直接将 `TeamAgentException` 重命名为 `TogoException`
- 一次性修改所有 import 和捕获逻辑
- 若担心短期兼容，可在 `src/exception.py` 中短暂保留：
  - `TeamAgentException = TogoException`

推荐做法：

- 首次提交中保留别名兼容
- 后续稳定后再删除兼容别名

### 决策三：内部 Agent 语义不随品牌名变更

`TogoSpace` 是产品名称，不等价于底层“Agent”概念。因此以下内容不纳入本次重命名：

- `agentService`
- `agentTurnRunner`
- `Agent` 模型
- `get_togo_agents()`
- 各类 agent 相关 API 路径和字段名

## 分阶段实施计划

### 第一阶段：用户可见名称统一

目标：

- 完成品牌名的第一层统一，让应用界面和 README 先切换到 `TogoSpace`

修改内容：

- 托盘标题
- 前端页面标题
- README 中的产品名称和下载说明

验收标准：

- 启动应用后托盘显示 `TogoSpace`
- 打开前端页签标题显示 `TogoSpace`
- 中英文 README 的首屏名称一致

### 第二阶段：异常类收敛

目标：

- 将业务异常类统一为 `TogoException`

修改内容：

- `src/exception.py` 中定义新类名
- 全量修改 import、`isinstance`、继承链
- 更新断言异常继承关系
- 修正文档中的 `TogoAgentException` / `TeamAgentException`

验收标准：

- 后端可正常启动
- HTTP controller 的业务异常仍能被统一捕获
- `assertUtil` 触发的异常仍按预期返回业务错误

### 第三阶段：打包与发布产物改名

目标：

- 对安装包、Release 标题和 App Bundle 完成统一改名

修改内容：

- `TogoAgent.app -> TogoSpace.app`
- zip 产物名同步
- PyInstaller spec 中的 bundle 名称和 identifier 更新

验收标准：

- 构建脚本生成 `TogoSpace-<version>.app`
- release 脚本识别新文件名

注意事项：

- `bundle_identifier` 从 `com.togoagent.app` 改为 `com.togospace.app` 后，macOS 会将其视为新应用
- 如需平滑覆盖安装，应评估是否暂时保留旧 bundle identifier

### 第四阶段：Docker 与发布链路统一

目标：

- 统一镜像名、容器名、volume 名和相关文档

修改内容：

- `togoagent` 镜像标签
- `togoagent` 容器名
- `togoagent-data` volume 名
- GitHub Actions docker workflow 中的 tag

验收标准：

- `docker compose config` 能通过
- Docker workflow 配置中的 tag 一致

注意事项：

- 若 Docker Hub 仓库尚未创建 `togospace`，需要先确认发布策略
- 可考虑短期同时保留 `togoagent` 与 `togospace` 两套 tag

### 第五阶段：运行时目录兼容

目标：

- 引入 `~/.togospace` 的同时不破坏现有用户数据

修改内容：

- `src/appPaths.py` 中调整 frozen 模式目录解析逻辑
- 相关说明文档改为新目录优先

推荐策略：

1. 开发模式继续使用 `repo/dev_storage_root/`
2. frozen 模式优先读 `~/.togospace`
3. 若 `~/.togospace` 不存在且 `~/.togo_agent` 存在，则回退到旧目录

验收标准：

- 新安装用户可在 `~/.togospace` 下正常运行
- 老用户保留旧目录时可继续启动并读取原配置

### 第六阶段：文档与元数据收尾

目标：

- 统一对内文档、测试说明和仓库元数据

修改内容：

- `docs/DOCKER.md`
- `docs/RELEASE_HANDBOOK.md`
- 测试配置说明中的 `~/.togo_agent`
- `.gitmodules` 中的前端子模块地址
- 宣传文案中的项目名称和仓库地址

验收标准：

- `rg` 扫描后仅剩允许保留的历史引用

## 建议保留不改的内容

以下内容建议本次不改，避免收益不足的大面积扩散：

- `TEAMAGENT_PRESET_DIR`
- service / model 中与业务语义相关的 `Agent` 命名
- 旧版本文档中大量历史截图和叙述性文本
- `dist/`、`build/` 中的历史构建产物

## 风险与缓解

### 风险一：用户目录切换导致“配置丢失”错觉

缓解：

- 引入旧目录回退逻辑
- 在发布说明中明确说明目录策略变化

### 风险二：异常类重命名遗漏 import

缓解：

- 使用全仓搜索确认所有 import 与引用点
- 改名后执行后端启动和测试回归

### 风险三：macOS App 被识别为新应用

缓解：

- 在改 `bundle_identifier` 前确认是否接受新应用身份
- 若不接受，可先只改显示名和产物名，后续单独切换 identifier

### 风险四：Docker 拉取命令失效

缓解：

- 文档和 CI 同步变更
- 必要时短期保留旧 tag

## 验证清单

### 基础验证

- 后端启动成功
- TUI / 托盘名称显示 `TogoSpace`
- Web Console 页签标题显示 `TogoSpace`

### 异常验证

- 业务异常仍能被 `BaseHandler` 识别并转为业务响应
- `MakeSureException` 继承链工作正常

### 构建验证

- `scripts/build_mac.py` 能生成新名称 app
- `scripts/build_release.py` 能识别新名称产物

### Docker 验证

- `docker compose config` 通过
- Dockerfile 中环境变量和文档示例一致

### 文档一致性验证

- 运行 `rg -n "TogoAgent|togoagent|togo_agent|TeamAgentException|TogoAgentException"` 后，仅保留明确允许的历史引用

## 实施顺序建议

建议拆为两批提交：

### 第一批

- `TogoSpace` 用户可见名称
- `TogoException` 内部异常改名

特点：

- 影响最直接
- 便于快速验证
- 不涉及老用户数据迁移

### 第二批

- 打包产物名
- Docker 命名
- 运行时目录兼容
- 文档和元数据收尾

特点：

- 影响部署和升级路径
- 需要更完整的回归验证

## 当前结论

本次改名可以执行，但建议按“品牌名优先、异常类单独收敛、运行目录兼容处理”的顺序落地。

最关键的两个实现点是：

1. `TeamAgentException -> TogoException`
2. `~/.togo_agent -> ~/.togospace` 必须带兼容策略，不能直接硬切
