/**
 * 与后端 core/events.py 一一对应。
 *
 * 这个文件是前后端之间唯一的「合同」。后端 EventType 加一类，
 * 这里必须同步加，否则那份数据会被当成未知事件丢掉（而不是崩掉——见 reducer 的 default 分支）。
 */

export type EventType =
  | "text"
  | "tool_call"
  | "tool_result"
  | "approval_request"
  | "usage"
  | "todo"
  | "notice"
  | "error"
  | "done";

/** 后端推送的原始事件：type 加扁平的字段。缺哪个字段取决于 type。 */
export interface RawEvent {
  type: EventType;
  delta?: string;
  id?: string;
  name?: string;
  arguments?: Record<string, unknown>;
  content?: string;
  is_error?: boolean;
  reason?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  /** 第 18 步起 usage 事件携带的观测字段 */
  round_no?: number;
  cache_hit_tokens?: number;
  cache_miss_tokens?: number;
  duration_ms?: number;
  first_token_ms?: number;
  context?: Record<string, number>;
  /** todo 事件携带的全量清单快照 */
  items?: TodoItem[];
  message?: string;
  session_id?: string;
}

/** 开发者控制台（第 26 步）：GET /console/overview 的返回结构 */
export interface ConsoleOverview {
  usage: UsageSummary;
  by_model: Array<{
    model: string;
    calls: number;
    total_tokens: number;
    prompt_tokens: number;
    completion_tokens: number;
  }>;
  tool_stats: Array<{ name: string; calls: number; errors: number }>;
  tool_calls_total: number;
  tool_errors_total: number;
  by_session: Array<{ session_id: string; llm_calls: number; total_tokens: number }>;
}

/** 任务清单的一条。与后端 memory/todo.py 的 list_items() 返回一致。 */
export interface TodoItem {
  id: number;
  position: number;
  content: string;
  status: "pending" | "in_progress" | "done";
}

/** 助手消息里的一个块。消息必须建模成「有序块列表」，不能是一条字符串：
 *  text 是增量流，而工具调用插在文本中间，拼成一个字符串就再也分不清顺序了。 */
export type Block =
  | { kind: "text"; text: string }
  | {
      kind: "tool";
      callId: string;
      name: string;
      args: Record<string, unknown>;
      result?: string;
      isError?: boolean;
      state: "running" | "done";
    }
  | { kind: "notice"; message: string }
  | { kind: "error"; message: string };

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  blocks: Block[];
  /** 流式进行中的那条：用来决定要不要显示「思考中」和光标 */
  streaming?: boolean;
}

export interface SessionSummary {
  session_id: string;
  title: string;
  updated_at: string;
  messages: number;
}

export interface Health {
  ok: boolean;
  model: string;
  workspace: string;
  tools: string[];
  pending_approvals: number;
}

export interface PendingApproval {
  callId: string;
  name: string;
  args: Record<string, unknown>;
  reason: string;
}

/** 后端历史里只有 role/content（工具往返不落库），回放时转成单块消息。 */
export interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
}

/** 一次模型调用的用量流水（GET /usage 的 calls 项）。 */
export interface UsageCall {
  id: number;
  session_id: string;
  created_at: string;
  round_no: number;
  model: string;
  prompt_tokens: number;
  cache_hit_tokens: number;
  cache_miss_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  duration_ms: number;
  first_token_ms: number;
  ctx_system: number;
  ctx_history: number;
  ctx_question: number;
  ctx_tool_results: number;
}

/** GET /usage 的 summary。cache_hit_rate 是 0~1 的小数。 */
export interface UsageSummary {
  calls: number;
  prompt_tokens: number;
  cache_hit_tokens: number;
  cache_miss_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cache_hit_rate: number;
  avg_first_token_ms: number;
  total_duration_ms: number;
}
