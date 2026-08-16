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
}

export interface AgentDetail extends AgentInfo {
  agent_name: string;
  driver_type: string;
  prompt: string;
  error_message?: string | null;
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

export interface AgentTask {
  id: number;
  team_id: number;
  title: string;
  description: string;
  assignee_id: number;
  creator_id: number;
  manager_id: number | null;
  status: AgentTaskStatus;
  priority: AgentTaskPriority;
  parent_id: number | null;
  depends_on: number[];
  room_id: number | null;
  result: string;
  block_reason: string;
  created_at: string | null;
  updated_at: string | null;
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
