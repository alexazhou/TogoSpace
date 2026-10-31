export type AgentStatus = 'active' | 'idle' | 'failed' | 'closed';
export type AgentActivityType =
  | 'llm_infer'
  | 'tool_call'
  | 'compact'
  | 'agent_state'
  | 'reasoning'
  | 'chat_reply'
  | 'message_received'
  | 'task_received'
  | 'unknown';
export type AgentActivityStatus = 'started' | 'succeeded' | 'failed' | 'cancelled';
export type RoomType = 'private' | 'group';
export type I18nText = Record<string, string>;
export type EntityI18n = Record<string, I18nText>;

/** 执行环境：宿主 or 容器（内存态，后端重启一律回宿主）。 */
export type AgentExecEnv = 'host' | 'container';

/**
 * 自建容器信息（对应后端 `model/coreModel/containerModel.py::ContainerInfo`）。
 *
 * 注意两个与直觉不同的点（照后端 `model_dump(mode="json")` 的真实字段，别按文档 §8 的字面猜）：
 * 1. 容器 ID 字段名叫 `docker_id`，不是 `container_id`；预记录尚未回填时为 null。
 * 2. 没有 `is_managed`；表示「当前所处环境」的是 `is_entered`。
 *
 * `docker_id` 可空是**契约的一部分**：创建流程先落 `docker_id=NULL` 的预记录，
 * `docker create` 成功后才回填 12 位短 ID。预记录在列表里也可见，此时只能用
 * `record_id` / `container_name` 标识。不要写成非空——`vue-tsc --noEmit` 会卡构建，
 * 且运行期预记录行必然命中这条分支。
 */
export interface ContainerInfo {
  /** `agent_containers.id`，前端表格行 key；预记录没有 docker_id 时用它删除。 */
  record_id: number;
  agent_id: number;
  /** Docker 12 位短 ID；预记录为 null。 */
  docker_id: string | null;
  container_name: string;
  image: string;
  /** Docker `State.Status` 原值，直接展示不做翻译；查不到为 missing，inspect 失败为 unknown。 */
  state: string;
  /** 该容器是否为该 Agent 当前所处的执行环境。 */
  is_entered: boolean;
  /**
   * 记录创建时间，ISO 8601 字符串（`model_dump(mode="json")` 把 `datetime` 序列化的结果），
   * 未创建完成或后端未给时为 null。**不是**文档 §2.1 写的 int 时间戳。
   */
  created_at: string | null;
}

export interface AgentInfo {
  id?: number | null;
  name: string;
  i18n: EntityI18n;
  employee_number?: number | null;
  role_template_id?: number | null;
  model: string;
  team_id?: number | null;
  status: AgentStatus;
  employ_status?: string | null;
  driver?: string;
  special?: 'operator' | 'system' | null;
  allow_tools?: string[] | null;
  allow_skills?: string[] | null;
  /** 当前执行环境；未加载的 Agent 后端恒给 host，此处仍按可空容错。 */
  exec_env?: AgentExecEnv | null;
  /** 当前所在容器的 12 位短 ID；宿主环境为 null。 */
  current_container_id?: string | null;
  /** `deleted=0` 的活跃自建容器数（含创建中的预记录），用于「n/3」徽标。 */
  container_count?: number | null;
}

export interface AgentDetail extends AgentInfo {
  agent_name: string;
  driver_type: string;
  prompt: string;
  error_message?: string | null;
  /** Agent 详情额外携带的实时容器列表；预记录 `docker_id` 为 null。 */
  containers?: ContainerInfo[] | null;
}

export interface AgentActivity {
  id: number;
  agent_id: number;
  team_id: number;
  activity_type: AgentActivityType;
  status: AgentActivityStatus;
  title: string;
  detail: string;
  error_message?: string | null;
  started_at: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  metadata: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
}

export type AgentTaskStatus =
  | 'TODO'
  | 'PENDING'
  | 'IN_PROGRESS'
  | 'REVIEWING'
  | 'ON_HOLD'
  | 'DONE'
  | 'CANCELLED';

export type AgentTaskPriority = 'HIGH' | 'NORMAL' | 'LOW';

/**
 * 任务事件的性质，由 from_status / to_status 的空值组合天然决定（后端不落类型字段）。
 *
 * - `create`     (null, 非null) 正常创建
 * - `snapshot`   (null, 非null) 且 content 以 MIGRATION_CONTENT_PREFIX 开头：存量迁移快照
 * - `transition` (非null, 非null) 状态流转
 * - `comment`    (null, null) 工单评论
 *
 * 非法组合 (非null, null) 服务端入库前已拒绝，前端不处理。
 */
export type AgentTaskEventKind = 'create' | 'snapshot' | 'transition' | 'comment';

/** 存量迁移快照事件的固定内容前缀（0015 结转段写入，后端暂不剥离）。 */
export const MIGRATION_CONTENT_PREFIX = '【系统迁移自动生成】';

export interface AgentTaskEvent {
  id: number;
  task_id: number;
  /** 操作人 Agent ID；`-2` = 系统（SpecialAgent.SYSTEM），`-1` = 操作者（SpecialAgent.OPERATOR）。 */
  actor_id: number;
  from_status: AgentTaskStatus | null;
  to_status: AgentTaskStatus | null;
  content: string;
  created_at: string | null;
  updated_at: string | null;
}

/** 任务详情中的依赖摘要（`get_task_detail` 返回；任务已删除时 status 为 'UNKNOWN'）。 */
export interface AgentTaskDependencySummary {
  task_id: number;
  title: string;
  status: AgentTaskStatus | 'UNKNOWN';
}

export interface AgentTask {
  id: number;
  team_id: number;
  title: string;
  description: string;
  assignee_id: number;
  creator_id: number;
  /** 验收人（原 `manager_id`；注意部门域 `DeptTreeNode.manager_id` 与此无关）。 */
  reviewer_id: number | null;
  status: AgentTaskStatus;
  priority: AgentTaskPriority;
  parent_id: number | null;
  depends_on: number[];
  room_id: number | null;
  created_at: string | null;
  updated_at: string | null;
  /** 以下字段仅详情接口返回，列表接口不带，故可选。 */
  events?: AgentTaskEvent[];
  events_total?: number;
  last_note?: string;
  current_block_reason?: string | null;
  depends_on_details?: AgentTaskDependencySummary[];
}

export interface RoomInfo {
  room_id: number;
  room_name: string;
  i18n: EntityI18n;
  room_type: RoomType;
  state: string;
  need_scheduling: boolean;
  agents: number[];
  tags?: string[];
  biz_id?: string | null;
  current_turn_agent_id: number | null;
}

export interface MessageInfo {
  db_id: number | null;
  sender_id: number;
  sender_display_name: string;
  content: string;
  time: string;
  seq: number | null;
  insert_immediately: boolean;
}

export interface WsMessageEvent {
  event: 'message';
  gt_room: {
    id: number;
    team_id: number;
    name: string;
  };
  sender_id: number;
  content: string;
  time: string;
}

export interface WsAgentStatusEvent {
  event: 'agent_status';
  gt_agent: {
    id: number;
    name: string;
    team_id: number;
  };
  status: 'ACTIVE' | 'IDLE' | 'FAILED';
}

export interface WsAgentActivityEvent {
  event: 'agent_activity';
  activity?: AgentActivity;
  data?: AgentActivity;
}

export interface WsRoomStatusEvent {
  event: 'room_status';
  gt_room: {
    id: number;
    team_id: number;
    name: string;
  };
  state: 'SCHEDULING' | 'IDLE';
  current_turn_agent_id: number | null;
  need_scheduling: boolean;
}

export interface WsScheduleStateEvent {
  event: 'schedule_state';
  schedule_state: 'STOPPED' | 'BLOCKED' | 'RUNNING';
  not_running_reason?: string;
}

export type WsEvent = WsMessageEvent | WsAgentStatusEvent | WsAgentActivityEvent | WsRoomStatusEvent | WsScheduleStateEvent;

export interface RoomState extends RoomInfo {
  preview: string;
  unread: number;
}

export interface RoomMemberProfile {
  id: number;
  name: string;
  i18n: EntityI18n;
  employee_number: number | null;
  role_template_name: string | null;
  is_leader: boolean;
}

export interface TeamSummary {
  id: number;
  name: string;
  i18n: EntityI18n;
  working_directory: string;
  config: Record<string, unknown>;
  max_function_calls: number | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface TeamRoomDetail {
  id: number;
  name: string;
  i18n: EntityI18n;
  type?: string;
  initial_topic: string | null;
  max_turns: number | null;
  agents: string[];
  agent_ids?: number[];
  biz_id?: string | null;
  tags?: string[];
}

export interface DeptTreeNode {
  id?: number | null;
  name: string;
  i18n?: EntityI18n;
  responsibility: string;
  manager_id: number | null;
  agent_ids: number[];
  children: DeptTreeNode[];
}

export interface FrontendDriverType {
  name: string;
  description: string;
}

export interface ModelSlotOption {
  key: string;
  value: string;
}

export const MODEL_SLOT_LABELS: Record<string, string> = {
  primary: '主模型',
  lite: '轻量模型',
  advanced: '高级模型',
  vision: '视觉模型',
};

export interface FrontendConfig {
  driver_types: FrontendDriverType[];
  model_slots: ModelSlotOption[];
  context_config_defaults: LlmContextConfig;
}

export interface DirectoriesConfig {
  config_dir: string;
  workspace_dir: string;
  data_dir: string;
  log_dir: string;
}

export interface TeamDetail extends TeamSummary {
  members: TeamMember[];
  rooms: TeamRoomDetail[];
}

export interface TeamPresetAgent {
  name: string;
  i18n?: EntityI18n;
  role_template: string;
  model?: string | null;
  driver?: string;
  allow_tools?: string[] | null;
}

export interface TeamPresetRoom {
  name: string;
  i18n?: EntityI18n;
  agents: string[];
  initial_topic?: string;
  max_rounds?: number | null;
  biz_id?: string | null;
  tags?: string[];
}

export interface TeamPresetDeptNode {
  dept_name: string;
  i18n?: EntityI18n;
  responsibility: string;
  manager: string;
  agents: string[];
  children: TeamPresetDeptNode[];
}

export interface TeamPresetRule {
  name: string;
  i18n?: EntityI18n;
  soul: string;
  prompt_file?: string;
  model?: string | null;
}

export interface TeamPresetExport {
  uuid?: string | null;
  name: string;
  i18n?: EntityI18n;
  config: Record<string, unknown>;
  rule_templates: TeamPresetRule[];
  agents: TeamPresetAgent[];
  dept_tree?: TeamPresetDeptNode | null;
  preset_rooms: TeamPresetRoom[];
  auto_start: boolean;
  is_default?: boolean;
}

export interface TeamMember {
  id: number;
  name: string;
  i18n: EntityI18n;
  employee_number: number;
  role_template_id: number;
}

export interface CreateTeamPayload {
  name: string;
  working_directory: string;
  config: Record<string, unknown>;
  members?: Array<{
    name: string;
    role_template: string;
  }>;
  preset_rooms?: Array<{
    name: string;
    members: string[];
    initial_topic: string;
    max_turns?: number | null;
  }>;
}

export interface RoleTemplateSummary {
  id: number;
  name: string;
  i18n: EntityI18n;
  soul?: string;
  type?: string | null;
}

export interface RoleTemplateDetail extends RoleTemplateSummary {
  soul: string;
}

export type LlmServiceType = string;




export interface SkillInfo {
  name: string;
  description: string;
  is_builtin: boolean;
  files: string[];
}

export interface LlmContextConfig {
  compact_trigger_ratio?: number | null;
  reserve_output_tokens?: number | null;
  context_window_tokens?: number | null;
  compact_summary_max_tokens?: number | null;
}

export interface LlmModelConfig {
  name: string;
  protocol: string;
  /** 支持的输入类型，如 ['text', 'image']。缺省为纯文本 ['text']。 */
  input?: string[] | null;
  context_config: LlmContextConfig | null;
  extra_headers: Record<string, string> | null;
  extra_params: Record<string, unknown> | null;
}

export interface LlmProviderConfig {
  name: string;
  enable: boolean;
  type: LlmServiceType;
  api_key: string;
  urls: Record<string, string>;
  models: LlmModelConfig[];
}

export interface DefaultModelSlots {
  primary: string | null;
  lite: string | null;
  vision: string | null;
  advanced: string | null;
}

export interface LlmConfigPayload {
  llm_providers: LlmProviderConfig[];
  default_models: DefaultModelSlots;
  context_config: LlmContextConfig;
}

export interface LlmTestResult {
  status: 'ok' | 'error';
  message: string;
  detail?: {
    duration_ms: number;
    response_text: string;
    raw_error: string;
  };
}

export interface DeepSeekThirdPartyServiceConfig {
  enabled: boolean;
  api_key: string;
}

export interface XiaomiMiMoThirdPartyServiceConfig {
  enabled: boolean;
  api_key: string;
}

export type ThirdPartySearchService = 'deepseek' | 'xiaomi_mimo';

export interface DefaultServiceConfig {
  search: ThirdPartySearchService;
}

export interface ThirdPartyServicesConfigPayload {
  third_party_services: {
    default_service: DefaultServiceConfig;
    deepseek: DeepSeekThirdPartyServiceConfig;
    xiaomi_mimo: XiaomiMiMoThirdPartyServiceConfig;
  };
}

export interface ThirdPartySearchSource {
  url: string;
  title?: string;
  summary?: string;
  site_name?: string;
  publish_time?: string;
  logo_url?: string;
}

export interface ThirdPartySearchResult {
  success: boolean;
  service: ThirdPartySearchService | string;
  content?: string;
  sources?: ThirdPartySearchSource[];
  duration_ms?: number;
  error_message?: string;
}
