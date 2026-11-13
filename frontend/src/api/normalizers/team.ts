import type { RawRoleTemplateSummary, RawTeamDetail, RawTeamMember, RawTeamRoomDetail, RawTeamSummary } from '../types/raw';
import { normalizeEntityI18n } from './common';
import type { RoleTemplateDetail, RoleTemplateSummary, TeamDetail, TeamMember, TeamRoomDetail, TeamSummary } from '../../types';

export function normalizeTeamSummary(team: RawTeamSummary): TeamSummary {
  return {
    id: Number(team.id ?? 0),
    name: String(team.name ?? ''),
    i18n: normalizeEntityI18n(team.i18n),
    working_directory: typeof team.working_directory === 'string' ? team.working_directory : '',
    config: typeof team.config === 'object' && team.config !== null
      ? team.config as Record<string, unknown>
      : {},
    max_function_calls: null,
    enabled: Boolean(team.enabled),
    created_at: String(team.created_at ?? ''),
    updated_at: String(team.updated_at ?? ''),
  };
}

function normalizeTeamMember(member: RawTeamMember): TeamMember {
  return {
    id: Number(member.id ?? 0),
    name: String(member.name ?? ''),
    i18n: normalizeEntityI18n(member.i18n),
    employee_number: Number(member.employee_number ?? 0),
    role_template_id: Number(member.role_template_id ?? 0),
  };
}

function normalizeTeamRoomDetail(room: RawTeamRoomDetail): TeamRoomDetail {
  const rawMaxTurns = typeof room.max_rounds === 'number'
    ? room.max_rounds
    : (typeof room.max_turns === 'number' ? room.max_turns : null);
  return {
    id: Number(room.id ?? 0),
    name: String(room.name ?? ''),
    i18n: normalizeEntityI18n(room.i18n),
    type: typeof room.type === 'string' ? room.type : undefined,
    initial_topic: typeof room.initial_topic === 'string' ? room.initial_topic : null,
    max_turns: rawMaxTurns,
    agents: Array.isArray(room.agents)
      ? room.agents.map((agent) => String(agent))
      : [],
    agent_ids: Array.isArray(room.agent_ids)
      ? room.agent_ids.map((agentId) => Number(agentId)).filter((agentId) => Number.isFinite(agentId))
      : [],
    biz_id: typeof room.biz_id === 'string' && room.biz_id.trim() ? room.biz_id : null,
    tags: Array.isArray(room.tags)
      ? room.tags.filter((tag): tag is string => typeof tag === 'string')
      : [],
  };
}

export function normalizeTeamDetail(team: RawTeamDetail): TeamDetail {
  return {
    ...normalizeTeamSummary(team),
    members: Array.isArray(team.agents) ? team.agents.map(normalizeTeamMember) : [],
    rooms: Array.isArray(team.rooms) ? team.rooms.map(normalizeTeamRoomDetail) : [],
  };
}

export function normalizeRoleTemplateSummary(template: RawRoleTemplateSummary): RoleTemplateSummary {
  return {
    id: Number(template.id ?? 0),
    name: String(template.name ?? ''),
    i18n: normalizeEntityI18n(template.i18n),
    soul: String(template.soul ?? ''),
    type: template.type,
  };
}

export function normalizeRoleTemplateDetail(template: RawRoleTemplateSummary, templateId?: number): RoleTemplateDetail {
  return {
    ...normalizeRoleTemplateSummary(template),
    id: Number(template.id ?? templateId ?? 0),
    soul: String(template.soul ?? ''),
  };
}
