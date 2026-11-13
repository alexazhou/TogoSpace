import type { AgentActivity, RoomState } from '../../../types';
import { displayName, stripAnsi } from '../../../utils';

/**
 * AgentActivityItem 的视图模型层（#213 抽出）。
 *
 * 这里只做「活动对象 → 展示字段」的纯映射：不依赖 Vue、不读组件 ref、不碰 DOM，
 * 因而可以脱离组件单独单测。所有 i18n 与房间名解析都由调用方注入：
 * - `t` 由组件传入（vue-i18n），本模块不 import i18n 实例，避免单测被 i18n 状态绑住；
 * - `resolveTaskRoomName` 由组件注入（组件才能读 runtimeStore），本模块不碰全局 store。
 */

export type Translate = (key: string, named?: Record<string, unknown>) => string;

export type ActivityViewInput = {
  activity: AgentActivity;
  t: Translate;
  /** 由 team_id + task_room_id 解析房间显示名，解析不到返回空串 */
  resolveTaskRoomName: (teamId: number, taskRoomId: number) => string;
};

export type ActivityView = {
  chatReplyContent: string;
  chatReplyIsLong: boolean;
  reasoningContent: string;
  durationText: string;
  executeBashDescription: string;
  displayToolName: string;
  executeBashCommand: string;
  executeBashResult: boolean;
  expandedContent: boolean;
  expandedMessage: boolean;
  expandedToolResult: boolean;
  execEnvBadge: string;
  execEnvBadgeTitle: string;
  taskRoomLabel: string;
  receivedMessages: Array<{ sender: string; content: string }>;
  exitCode: string;
  inlineTitle: boolean;
  metadataToolName: string;
  model: string;
  taskTitle: string;
  sendChatMsgContent: string;
  sendChatMsgError: string;
  sendChatMsgTruncated: boolean;
  sendMessagePrefix: string;
  retryHistoryText: string;
  retryErrorText: string;
  retryText: string;
  showRetryErrorMessage: boolean;
  startChatTarget: string;
  showErrorMessage: boolean;
  showSummary: boolean;
  showToolArguments: boolean;
  showToolName: boolean;
  startedAtText: string;
  stateSymbol: string;
  stderr: string;
  stdout: string;
  summary: string;
  summaryIsCode: boolean;
  summaryTitle: string;
  title: string;
  toolArguments: string;
  toolName: string;
  toolResult: string;
};

export function readTrimmedString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

export function safeTrimEnd(value: unknown): string {
  return typeof value === 'string' ? value.replace(/\s+$/, '') : '';
}

export function formatActivityTime(value: string | null | undefined): string {
  if (!value) {
    return '';
  }
  return value.replace('T', ' ').slice(0, 19);
}

export function formatDuration(durationMs: number | null | undefined): string {
  if (durationMs == null || Number.isNaN(durationMs) || durationMs < 0) {
    return '0ms';
  }
  if (durationMs < 1000) {
    return `${durationMs}ms`;
  }
  return `${(durationMs / 1000).toFixed(durationMs >= 10_000 ? 0 : 1)}s`;
}

export function computeToolName(activity: AgentActivity): string {
  const metadataToolName = activity.metadata?.tool_name;
  if (typeof metadataToolName === 'string' && metadataToolName.trim()) {
    return metadataToolName.trim();
  }
  const detail = activity.detail.trim();
  return activity.activity_type === 'tool_call' ? detail : '';
}

export function computeRoomName(activity: AgentActivity): string {
  // send_chat_msg 的目标房间存放在 tool_arguments.room_name
  const toolArguments = activity.metadata?.tool_arguments;
  if (toolArguments && typeof toolArguments === 'object') {
    const argRoomName = (toolArguments as { room_name?: unknown }).room_name;
    if (typeof argRoomName === 'string' && argRoomName.trim()) {
      return argRoomName.trim();
    }
  }
  return '';
}

export function activityStatusSymbol(status: AgentActivity['status']): string {
  if (status === 'started') {
    return '●';
  }
  if (status === 'succeeded') {
    return '✓';
  }
  return '×';
}

export function shouldShowToolName(activity: AgentActivity, toolName: string): boolean {
  return activity.activity_type === 'tool_call'
    && toolName !== 'send_chat_msg'
    && toolName !== 'create_task'
    && toolName !== 'update_task'
    && toolName !== 'finish_action'
    && toolName !== 'start_chat'
    && toolName.length > 0;
}

export function displayToolName(t: Translate, toolName: string): string {
  if (!toolName) {
    return '';
  }
  const i18nKey = `agent.toolNames.${toolName}`;
  const translated = t(i18nKey);
  // 如果翻译后的内容包含 key 路径，说明没有定义翻译，回退到原始名称
  return translated !== i18nKey ? translated : toolName;
}

export function activityTitle(t: Translate, activity: AgentActivity, toolName: string): string {
  if (activity.activity_type === 'agent_state') {
    const detail = activity.detail.trim().toUpperCase();
    if (detail === 'ACTIVE') {
      return t('agent.activityType.startActivity');
    }
    if (detail === 'IDLE') {
      return t('agent.activityType.stopAction');
    }
  }

  if (activity.activity_type === 'tool_call') {
    if (toolName === 'send_chat_msg') {
      return t('agent.activityType.sendMessage');
    }
    if (toolName === 'create_task') {
      return t('agent.activityType.createTask');
    }
    if (toolName === 'update_task') {
      return t('agent.activityType.updateTask');
    }
    if (toolName === 'finish_action') {
      return t('agent.activityType.finishAction');
    }
    if (toolName === 'start_chat') {
      return t('agent.activityType.startChat');
    }
  }

  switch (activity.activity_type) {
    case 'agent_state':
      return t('agent.activityType.agentState');
    case 'llm_infer':
      return t('agent.activityType.llmInfer');
    case 'reasoning':
      return t('agent.activityType.reasoning');
    case 'chat_reply':
      return t('agent.activityType.chatReply');
    case 'tool_call':
      return t('agent.activityType.toolCall');
    case 'compact':
      return t('agent.activityType.compact');
    case 'message_received':
      return t('agent.activityType.messageReceived');
    case 'task_received':
      return t('agent.activityType.taskReceived');
    case 'unknown':
      return t('agent.activityType.unknown');
    default:
      return t('agent.activityType.unknown');
  }
}

export function getActivityTaskTitle(t: Translate, activity: AgentActivity, toolName: string): string {
  if (activity.activity_type === 'task_received') {
    return readTrimmedString(activity.detail);
  }

  if (toolName !== 'create_task' && toolName !== 'update_task') {
    return '';
  }

  // 优先从 tool_result 中取（后端通常会返回最新的 task 对象）
  const toolResult = activity.metadata?.tool_result;
  if (toolResult && typeof toolResult === 'object') {
    // 兼容两种格式：直接在 result 中，或在 result.task 中
    const title = readTrimmedString((toolResult as { title?: unknown }).title)
      || readTrimmedString((toolResult as { task?: { title?: unknown } }).task?.title);
    if (title) {
      return title;
    }
  }

  // 兜底：如果是 create_task，可以从参数里取
  if (toolName === 'create_task') {
    const toolArguments = activity.metadata?.tool_arguments;
    if (toolArguments && typeof toolArguments === 'object') {
      const title = readTrimmedString((toolArguments as { title?: unknown }).title);
      if (title) {
        return title;
      }
    }
  }

  return '';
}

export function getReceivedMessages(activity: AgentActivity): Array<{ sender: string; content: string }> {
  const messages = activity.metadata?.messages;
  if (!Array.isArray(messages)) {
    return [];
  }
  return messages.filter(
    (m): m is { sender: string; content: string } =>
      m != null && typeof m.sender === 'string' && typeof m.content === 'string',
  );
}

export function getActivityToolCommand(activity: AgentActivity): string {
  const command = activity.metadata?.command;
  return typeof command === 'string' ? command : '';
}

export function getExecuteBashDescription(activity: AgentActivity, toolName: string): string {
  if (activity.activity_type !== 'tool_call' || toolName !== 'execute_bash') {
    return '';
  }
  const toolArguments = activity.metadata?.tool_arguments;
  if (!toolArguments || typeof toolArguments !== 'object') {
    return '';
  }
  return readTrimmedString((toolArguments as { description?: unknown }).description);
}

export function getActivityToolArguments(t: Translate, activity: AgentActivity, toolName: string): string {
  const toolArguments = activity.metadata?.tool_arguments;
  if (toolArguments == null) {
    return '';
  }

  if (typeof toolArguments !== 'object' || toolArguments === null) {
    return t('agent.parseFailed');
  }

  if (toolName === 'execute_bash') {
    const command = readTrimmedString((toolArguments as { command?: unknown }).command);
    if (command) {
      return command;
    }
  }

  if (toolName === 'write_file' || toolName === 'read_file' || toolName === 'edit') {
    const filePath = readTrimmedString((toolArguments as { file_path?: unknown }).file_path);
    if (filePath) {
      return filePath;
    }
  }

  if (toolName === 'create_container') {
    const image = readTrimmedString((toolArguments as { image?: unknown }).image);
    return image || '';
  }

  if (toolName === 'enter_container') {
    return readTrimmedString((toolArguments as { container_id?: unknown }).container_id);
  }

  if (toolName === 'list_containers' || toolName === 'exit_to_host') {
    return '';
  }

  if (toolName === 'get_time') {
    return readTrimmedString((toolArguments as { timezone?: unknown }).timezone);
  }

  if (toolName === 'get_room_info') {
    return readTrimmedString((toolArguments as { room_name?: unknown }).room_name);
  }

  // 无参数工具不展示 `{}`，避免活动行出现无意义的参数胶囊。
  if (Object.keys(toolArguments as Record<string, unknown>).length === 0) {
    return '';
  }

  return JSON.stringify(toolArguments);
}

export function getSendMessageContent(t: Translate, activity: AgentActivity): string {
  const toolArguments = activity.metadata?.tool_arguments;
  if (toolArguments == null || typeof toolArguments !== 'object') {
    return toolArguments == null ? '' : t('agent.parseFailed');
  }

  const candidate = toolArguments as { msg?: unknown; content?: unknown; text?: unknown };
  const message = [candidate.msg, candidate.content, candidate.text]
    .map((item) => readTrimmedString(item))
    .find(Boolean);
  return message || t('agent.parseFailed');
}

export function getSendMessagePrefix(t: Translate, roomName: string): string {
  if (!roomName) {
    return '';
  }
  return t('agent.sendToRoomPrefix', { room: roomName });
}

export function getStartChatTarget(t: Translate, activity: AgentActivity, toolName: string): string {
  if (toolName !== 'start_chat') {
    return '';
  }
  const toolArguments = activity.metadata?.tool_arguments;
  if (!toolArguments || typeof toolArguments !== 'object') {
    return '';
  }
  const agentName = readTrimmedString((toolArguments as { agent_name?: unknown }).agent_name);
  return agentName ? t('agent.startChatTarget', { agent: agentName }) : '';
}

export function getTaskRoomLabel(
  t: Translate,
  activity: AgentActivity,
  toolName: string,
  resolveTaskRoomName: ActivityViewInput['resolveTaskRoomName'],
): string {
  if (
    activity.activity_type !== 'message_received'
    && !(activity.activity_type === 'tool_call' && toolName === 'finish_action')
  ) {
    return '';
  }
  const taskRoomId = activity.metadata?.task_room_id;
  if (typeof taskRoomId !== 'number') {
    return '';
  }
  const taskRoomName = resolveTaskRoomName(activity.team_id, taskRoomId);
  if (!taskRoomName) {
    return '';
  }
  return t('agent.finishActionRoomLabel', { room: taskRoomName });
}

export function getExecuteBashStdout(activity: AgentActivity): string {
  const toolResult = activity.metadata?.tool_result;
  if (!toolResult || typeof toolResult !== 'object') {
    return '';
  }
  return stripAnsi(safeTrimEnd((toolResult as { stdout?: unknown }).stdout));
}

export function getExecuteBashStderr(activity: AgentActivity): string {
  const toolResult = activity.metadata?.tool_result;
  if (!toolResult || typeof toolResult !== 'object') {
    return '';
  }
  return stripAnsi(safeTrimEnd((toolResult as { stderr?: unknown }).stderr));
}

export function getExecuteBashExitCode(activity: AgentActivity): string {
  const toolResult = activity.metadata?.tool_result;
  if (!toolResult || typeof toolResult !== 'object') {
    return '';
  }
  const exitCode = (toolResult as { exit_code?: unknown }).exit_code;
  return typeof exitCode === 'number' ? `return_code=${exitCode}` : '';
}

export function getActivityToolResult(t: Translate, activity: AgentActivity, toolName: string): string {
  if (toolName === 'execute_bash') {
    return [getExecuteBashStdout(activity), getExecuteBashStderr(activity)].filter(Boolean).join('\n');
  }

  if (toolName === 'write_file') {
    const toolArguments = activity.metadata?.tool_arguments;
    if (toolArguments && typeof toolArguments === 'object') {
      const candidate = toolArguments as { content?: unknown; text?: unknown };
      const writeContent = [candidate.content, candidate.text]
        .map((item) => stripAnsi(safeTrimEnd(item)))
        .find(Boolean);
      if (writeContent) {
        return writeContent;
      }
    }
  }

  const toolResult = activity.metadata?.tool_result;
  if (toolResult == null) {
    return '';
  }
  if (typeof toolResult === 'string') {
    return stripAnsi(toolResult.replace(/\s+$/, ''));
  }
  if (typeof toolResult === 'number' || typeof toolResult === 'boolean') {
    return String(toolResult);
  }
  if (typeof toolResult === 'object') {
    const candidate = toolResult as { message?: unknown; content?: unknown; text?: unknown };
    const preferredText = [candidate.message, candidate.content, candidate.text]
      .map((item) => stripAnsi(safeTrimEnd(item)))
      .find(Boolean);
    if (preferredText) {
      return preferredText;
    }
    try {
      return JSON.stringify(toolResult, null, 2);
    } catch {
      return t('agent.parseFailed');
    }
  }
  return t('agent.parseFailed');
}

/**
 * 执行环境徽标文案（§8：容器状态直接展示 Docker 原值，不翻译）。
 *
 * 后端 `AgentActivityMeta.to_dict()` 会剔掉值为 None 的字段，所以宿主态下
 * `current_container_id` / `container_name` 的**键可能直接不存在**（不是 null），
 * 判断环境只能认 `metadata.exec_env` 本身，不能拿 `=== null` 猜。
 * 宿主态是默认环境，不渲染徽标；三键全缺失（老记录、Native driver 不带该字段）
 * 时同样返回空串 ⇒ 不渲染任何徽标。
 */
export function execEnvBadgeText(t: Translate, activity: AgentActivity): string {
  if (activity.metadata?.exec_env !== 'container') {
    return '';
  }
  const containerName = readTrimmedString(activity.metadata?.container_name);
  if (containerName) {
    return containerName;
  }
  const containerId = readTrimmedString(activity.metadata?.current_container_id);
  if (containerId) {
    // 后端正常回填 12 位短 ID；长度不合预期时截断成短 ID，避免撑爆 chip。
    return containerId.slice(0, 12);
  }
  // 环境已知是容器，但容器标识缺失：给环境名而不是空白胶囊。
  return t('agent.execEnv.container');
}

/** 徽标 hover 提示：容器态给完整容器 ID（徽标里是截断的短 ID）。 */
export function execEnvBadgeTitle(t: Translate, activity: AgentActivity): string {
  const containerName = readTrimmedString(activity.metadata?.container_name);
  const containerId = readTrimmedString(activity.metadata?.current_container_id);
  const identity = [containerName, containerId].filter(Boolean).join(' · ');
  return identity ? `${t('agent.execEnv.container')} · ${identity}` : t('agent.execEnv.container');
}

export function getActivityModel(activity: AgentActivity): string {
  const model = activity.metadata?.model;
  return typeof model === 'string' ? model : '';
}

export function getActivityRetryState(activity: AgentActivity): string {
  const retryState = activity.metadata?.request_state;
  return typeof retryState === 'string' ? retryState.trim().toUpperCase() : '';
}

export function getActivityRetryAttempt(activity: AgentActivity): number | null {
  const retryAttempt = activity.metadata?.retry_attempt;
  return typeof retryAttempt === 'number' ? retryAttempt : null;
}

export function getActivityRetryMaxAttempts(activity: AgentActivity): number | null {
  const retryMaxAttempts = activity.metadata?.retry_max_attempts;
  return typeof retryMaxAttempts === 'number' ? retryMaxAttempts : null;
}

export function getActivityRetryDelaySeconds(activity: AgentActivity): number | null {
  const retryDelaySeconds = activity.metadata?.retry_delay_seconds;
  return typeof retryDelaySeconds === 'number' && retryDelaySeconds > 0 ? retryDelaySeconds : null;
}

export function getActivityLastRetryError(activity: AgentActivity): string {
  if (activity.activity_type !== 'llm_infer') {
    return '';
  }
  return readTrimmedString(activity.metadata?.last_retry_error);
}

export function getActivityRetryText(t: Translate, activity: AgentActivity): string {
  if (activity.activity_type !== 'llm_infer') {
    return '';
  }
  const retryState = getActivityRetryState(activity);
  const attempt = getActivityRetryAttempt(activity);
  const maxAttempts = getActivityRetryMaxAttempts(activity);
  const delaySeconds = getActivityRetryDelaySeconds(activity);

  if (retryState === 'RETRY_SCHEDULED' && attempt !== null && maxAttempts !== null && delaySeconds !== null) {
    return t('agent.retryScheduled', { seconds: delaySeconds, attempt, max: maxAttempts });
  }
  if (retryState === 'RETRYING' && attempt !== null && maxAttempts !== null) {
    return t('agent.retryingProgress', { attempt, max: maxAttempts });
  }
  return '';
}

export function getActivityRetryHistoryText(t: Translate, activity: AgentActivity): string {
  if (activity.activity_type !== 'llm_infer') {
    return '';
  }
  const retryState = getActivityRetryState(activity);
  if (retryState) {
    return '';
  }
  const attempt = getActivityRetryAttempt(activity);
  if (attempt === null) {
    return '';
  }
  const retryCount = attempt - 1;
  if (retryCount <= 0) {
    return '';
  }
  if (activity.status === 'succeeded') {
    return t('agent.retrySucceededAfter', { count: retryCount });
  }
  if (activity.status === 'failed') {
    return t('agent.retryFailedAfter', { count: retryCount });
  }
  return t('agent.retryUsed', { count: retryCount });
}

export function getActivityToolName(activity: AgentActivity): string {
  const metadataToolName = activity.metadata?.tool_name;
  return typeof metadataToolName === 'string' ? metadataToolName : '';
}

export function activitySummary(
  t: Translate,
  activity: AgentActivity,
  summaryToolName: string,
  showToolName: boolean,
  toolArguments: string,
  toolCommand: string,
): string {
  if (activity.activity_type === 'tool_call') {
    if (summaryToolName === 'send_chat_msg' || summaryToolName === 'create_task' || summaryToolName === 'update_task') {
      return '';
    }
    if (summaryToolName === 'finish_action') {
      return '';
    }
    if (summaryToolName === 'execute_bash') {
      return getExecuteBashDescription(activity, summaryToolName);
    }
    if (showToolName && toolArguments) {
      return '';
    }
  }

  if (activity.activity_type === 'task_received') {
    return '';
  }

  if (activity.activity_type === 'chat_reply') {
    return '';
  }

  // 思考内容单独成块展示（默认三行），不再挤进单行摘要里当 `💭 ...` 截断。
  if (activity.activity_type === 'reasoning') {
    return '';
  }

  if ((activity.activity_type === 'llm_infer' || activity.activity_type === 'compact') && activity.status === 'failed') {
    return '';
  }

  if (toolCommand) {
    return toolCommand;
  }

  const detail = activity.detail.trim();
  if (activity.activity_type === 'agent_state' && (detail.toUpperCase() === 'ACTIVE' || detail.toUpperCase() === 'IDLE')) {
    return '';
  }
  if (detail && detail !== summaryToolName) {
    return detail;
  }

  return activity.error_message?.trim() ?? '';
}

/** 活动对象 → 模板直接用的一组展示字段。逐字段与拆分前的 computed 等价。 */
export function buildActivityView(input: ActivityViewInput): ActivityView {
  const { activity, t, resolveTaskRoomName } = input;
  const currentToolName = computeToolName(activity);
  const isFinishTurnActivity = activity.activity_type === 'tool_call' && currentToolName === 'finish_action';
  const currentTitle = activityTitle(t, activity, currentToolName);
  const currentToolCommand = getActivityToolCommand(activity);
  const currentToolArguments = getActivityToolArguments(t, activity, currentToolName);
  const currentModel = getActivityModel(activity);
  const currentMetadataToolName = getActivityToolName(activity);
  const currentDisplayToolName = displayToolName(t, currentToolName);
  const showToolName = shouldShowToolName(activity, currentToolName);
  const currentRoomName = computeRoomName(activity);
  const currentSendMessagePrefix = currentToolName === 'send_chat_msg'
    ? getSendMessagePrefix(t, currentRoomName)
    : '';
  const currentStartChatTarget = currentToolName === 'start_chat'
    ? getStartChatTarget(t, activity, currentToolName)
    : '';
  const currentTaskRoomLabel = getTaskRoomLabel(t, activity, currentToolName, resolveTaskRoomName);
  const executeBashResult = activity.activity_type === 'tool_call' && currentToolName === 'execute_bash';
  const currentExecuteBashDescription = executeBashResult
    ? getExecuteBashDescription(activity, currentToolName)
    : '';
  const hasExecuteBashDetails = executeBashResult
    && (Boolean(currentExecuteBashDescription) || Boolean(currentToolCommand));
  const currentStdout = executeBashResult ? getExecuteBashStdout(activity) : '';
  const currentStderr = executeBashResult ? getExecuteBashStderr(activity) : '';
  const currentExitCode = executeBashResult ? getExecuteBashExitCode(activity) : '';
  const currentToolResult = getActivityToolResult(t, activity, currentToolName);
  const currentErrorMessage = readTrimmedString(activity.error_message);
  const currentRetryText = getActivityRetryText(t, activity);
  const currentRetryHistoryText = getActivityRetryHistoryText(t, activity);
  const currentLastRetryError = getActivityLastRetryError(activity);
  const currentRetryErrorText = currentLastRetryError
    ? t('agent.retryError', { message: currentLastRetryError })
    : '';
  const expandedToolResult = activity.activity_type === 'tool_call'
    && currentToolName !== 'send_chat_msg'
    && (!isFinishTurnActivity || activity.status === 'failed')
    && Boolean(currentToolResult);
  const expandedMessage = activity.activity_type === 'tool_call' && currentToolName === 'send_chat_msg';
  const sendChatMsgContent = expandedMessage ? getSendMessageContent(t, activity) : '';
  const sendChatMsgTruncated = sendChatMsgContent.split('\n').length > 5 || sendChatMsgContent.length > 350;
  const sendChatMsgError = expandedMessage && activity.status === 'failed'
    ? (currentErrorMessage || currentToolResult)
    : '';
  const receivedMessages = getReceivedMessages(activity);
  const expandedContent = activity.activity_type === 'chat_reply' || expandedMessage || expandedToolResult
    || hasExecuteBashDetails
    || (activity.activity_type === 'message_received' && receivedMessages.length > 0);
  const currentTaskTitle = getActivityTaskTitle(t, activity, currentToolName);
  const currentExecEnvBadge = execEnvBadgeText(t, activity);
  const currentExecEnvBadgeTitle = currentExecEnvBadge ? execEnvBadgeTitle(t, activity) : '';
  const currentSummary = activitySummary(
    t,
    activity,
    currentToolName,
    showToolName,
    currentToolArguments,
    currentToolCommand,
  );
  const inlineTitle = (
    !(
      Boolean(currentSummary)
      || showToolName
      || (activity.activity_type !== 'tool_call' && Boolean(currentMetadataToolName))
    ) || activity.activity_type === 'message_received' || activity.activity_type === 'compact'
  ) && !(activity.activity_type === 'reasoning' && activity.detail.trim());
  const chatReplyContent = activity.activity_type === 'chat_reply' ? activity.detail.trim() : '';
  const chatReplyIsLong = chatReplyContent.split('\n').length > 5 || chatReplyContent.length > 350;
  // 思考内容默认展示三行（超出可展开），因此不看「是否很长」——三行以外的部分
  // 一律由 CSS 裁掉，按钮只在确实有被裁内容时出现。
  const reasoningContent = activity.activity_type === 'reasoning' ? activity.detail.trim() : '';

  return {
    chatReplyContent,
    chatReplyIsLong,
    reasoningContent,
    durationText: formatDuration(activity.duration_ms),
    executeBashDescription: currentExecuteBashDescription,
    displayToolName: currentDisplayToolName,
    executeBashCommand: executeBashResult && currentToolCommand ? `$ ${currentToolCommand}` : '',
    executeBashResult,
    expandedContent,
    expandedMessage,
    expandedToolResult,
    execEnvBadge: currentExecEnvBadge,
    execEnvBadgeTitle: currentExecEnvBadgeTitle,
    taskRoomLabel: currentTaskRoomLabel,
    receivedMessages,
    exitCode: currentExitCode,
    inlineTitle,
    metadataToolName: currentMetadataToolName,
    model: currentModel,
    taskTitle: currentTaskTitle,
    sendChatMsgContent,
    sendChatMsgError,
    sendChatMsgTruncated,
    sendMessagePrefix: currentSendMessagePrefix,
    retryHistoryText: currentRetryHistoryText,
    retryErrorText: currentRetryErrorText,
    retryText: currentRetryText,
    showRetryErrorMessage: activity.status !== 'failed' && Boolean(currentRetryErrorText) && currentLastRetryError !== currentErrorMessage,
    startChatTarget: currentStartChatTarget,
    showErrorMessage: Boolean(currentErrorMessage) && currentErrorMessage !== currentToolResult && !expandedMessage,
    showSummary: Boolean(currentSummary),
    showToolArguments: showToolName && Boolean(currentToolArguments) && !executeBashResult,
    showToolName,
    startedAtText: formatActivityTime(activity.started_at),
    stateSymbol: activityStatusSymbol(activity.status),
    stderr: currentStderr,
    stdout: currentStdout,
    summary: currentSummary,
    summaryIsCode: (executeBashResult && Boolean(currentExecuteBashDescription)) || (Boolean(currentToolCommand) && !executeBashResult),
    // bash 描述被压成单行（超出省略），展开态也保留 hover 看全文的机会。
    summaryTitle: expandedContent && !(executeBashResult && Boolean(currentExecuteBashDescription))
      ? ''
      : currentSummary,
    title: currentTitle,
    toolArguments: currentToolArguments,
    toolName: currentToolName,
    toolResult: currentToolResult,
  };
}

/** 房间名解析的默认实现依赖 runtimeStore；组件用它组装 resolveTaskRoomName。 */
export function resolveTaskRoomNameFromRooms(rooms: readonly RoomState[], taskRoomId: number): string {
  const room = rooms.find((item) => item.room_id === taskRoomId);
  if (!room) {
    return '';
  }
  return displayName({ name: room.room_name, i18n: room.i18n });
}
