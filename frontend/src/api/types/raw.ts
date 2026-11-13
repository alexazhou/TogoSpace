import type { AgentActivity, AgentInfo, AgentTask, DeptTreeNode, FrontendDriverType, RoleTemplateSummary } from '../../types';

export type RawRoomInfo = {
  gt_room?: {
    id?: unknown;
    name?: unknown;
    i18n?: unknown;
    type?: unknown;
    initial_topic?: unknown;
    biz_id?: unknown;
    tags?: unknown;
    max_turns?: unknown;
    agent_ids?: unknown;
  };
  state?: string;
  need_scheduling?: boolean;
  current_turn_agent_id?: unknown;
  agents?: unknown;
  agent_read_index?: unknown;
};

export type RawAgentInfo = Partial<AgentInfo> & {
  employee_number?: number;
  status?: string;
  role_template_id?: number;
  employ_status?: string;
  driver?: string;
  special?: 'operator' | 'system' | null;
  allow_tools?: string[] | null;
  allow_skills?: string[] | null;
};

/** 后端 Agent 列表 / 详情的运行时外壳：DB 字段在 `agent`，运行时字段在外层。 */
export type RawAgentRuntimeInfo = {
  agent?: RawAgentInfo;
  status?: string;
  exec_env_state?: {
    exec_env?: string;
    current_container_id?: string | null;
    container_name?: string | null;
  };
  container_count?: number | null;
  context_usage?: unknown;
};

export type RawAgentEntry = RawAgentInfo | RawAgentRuntimeInfo;

export type RawAgentActivity = Partial<AgentActivity> & {
  id?: unknown;
  agent_id?: unknown;
  team_id?: unknown;
  activity_type?: unknown;
  status?: unknown;
  title?: unknown;
  detail?: unknown;
  error_message?: unknown;
  started_at?: unknown;
  finished_at?: unknown;
  duration_ms?: unknown;
  metadata?: unknown;
  created_at?: unknown;
  updated_at?: unknown;
};

export type RawAgentTask = Partial<AgentTask> & {
  id?: unknown;
  team_id?: unknown;
  title?: unknown;
  description?: unknown;
  assignee_id?: unknown;
  creator_id?: unknown;
  reviewer_id?: unknown;
  status?: unknown;
  priority?: unknown;
  parent_id?: unknown;
  depends_on?: unknown;
  room_id?: unknown;
  created_at?: unknown;
  updated_at?: unknown;
  events?: unknown;
  events_total?: unknown;
  last_note?: unknown;
  current_block_reason?: unknown;
  depends_on_details?: unknown;
};

export type RawAgentTaskEvent = {
  id?: unknown;
  task_id?: unknown;
  actor_id?: unknown;
  from_status?: unknown;
  to_status?: unknown;
  content?: unknown;
  created_at?: unknown;
  updated_at?: unknown;
};

export type RawDeptTreeResponse = {
  dept_tree?: DeptTreeNode | null;
};

export type RawRoleTemplateSummary = Partial<RoleTemplateSummary> & {
  id?: unknown;
  name?: unknown;
  i18n?: unknown;
  soul?: unknown;
  type?: string | null;
};

export type RawTeamSummary = {
  id?: unknown;
  name?: unknown;
  i18n?: unknown;
  working_directory?: unknown;
  config?: unknown;
  enabled?: unknown;
  created_at?: unknown;
  updated_at?: unknown;
};

export type RawTeamMember = {
  id?: unknown;
  name?: unknown;
  i18n?: unknown;
  employee_number?: unknown;
  role_template_id?: unknown;
};

export type RawTeamRoomDetail = {
  id?: unknown;
  name?: unknown;
  i18n?: unknown;
  type?: unknown;
  initial_topic?: unknown;
  max_rounds?: unknown;
  max_turns?: unknown;
  agents?: unknown;
  agent_ids?: unknown;
  biz_id?: unknown;
  tags?: unknown;
};

export type RawTeamDetail = RawTeamSummary & {
  agents?: RawTeamMember[];
  rooms?: RawTeamRoomDetail[];
};

type RawFrontendDriverType = Partial<FrontendDriverType>;

export type RawFrontendConfig = {
  driver_types?: RawFrontendDriverType[];
  model_slots?: { key?: string; value?: string }[];
  context_config_defaults?: { context_window_tokens?: number; reserve_output_tokens?: number; compact_trigger_ratio?: number; compact_summary_max_tokens?: number };
};

export type RawMessageInfo = {
  id: number;
  room_id?: number;
  sender_id: number;
  content: string;
  send_time: string;
  /** 插入即分配，业务层恒有值（迁移 0019 会补齐存量）。 */
  seq: number;
  insert_immediately: boolean;
};

export type RawRoomLastMessageInfo = RawMessageInfo & {
  room_id: number;
};
