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
  message?: string;
  session_id?: string;
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
