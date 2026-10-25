/**
 * 对话状态机。
 *
 * 为什么用 reducer 而不是散落的 setState：流式渲染时事件是**异步、连续、可能乱序到达**的，
 * 十几次 setMessages 分散在回调里，很容易出现「工具结果盖到上一条消息上」这种问题。
 * 收敛成一个纯函数 applyEvent(message, event) 之后，任何一个事件的处理都能单独推理。
 */

import type { Block, ChatMessage, PendingApproval, RawEvent, SessionSummary, TodoItem } from "./types";

export interface ChatState {
  sessions: SessionSummary[];
  sessionId: string | null;
  messages: ChatMessage[];
  streaming: boolean;
  pending: PendingApproval[];
  todos: TodoItem[];
  lastTurnTokens: number;
  totalTokens: number;
  error: string | null;
}

export const initialState: ChatState = {
  sessions: [],
  sessionId: null,
  messages: [],
  streaming: false,
  pending: [],
  todos: [],
  lastTurnTokens: 0,
  totalTokens: 0,
  error: null,
};

export type Action =
  | { type: "sessions"; sessions: SessionSummary[] }
  | { type: "open"; sessionId: string; messages: ChatMessage[] }
  | { type: "new-session" }
  | { type: "send"; text: string; assistantId: string }
  | { type: "event"; event: RawEvent; assistantId: string }
  /** 打开会话后从 GET /sessions/{id}/todos 恢复的清单 */
  | { type: "todos"; items: TodoItem[] }
  | { type: "session-id"; sessionId: string }
  | { type: "stream-end" }
  | { type: "approval"; callId: string }
  | { type: "clear-error" }
  | { type: "error"; message: string };

export function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** 把后端回放的历史转成消息列表：库里只有 role/content，所以每条只有一个文本块。 */
export function historyToMessages(
  rows: { role: "user" | "assistant"; content: string }[],
): ChatMessage[] {
  return rows.map((row) => ({
    id: newId(),
    role: row.role,
    blocks: [{ kind: "text", text: row.content }],
  }));
}

/** 一个事件作用在「当前这条助手消息」上，返回新消息对象。纯函数，不改入参。 */
function applyEvent(message: ChatMessage, event: RawEvent): ChatMessage {
  const last = message.blocks[message.blocks.length - 1];

  switch (event.type) {
    case "text": {
      const delta = event.delta ?? "";
      if (!delta) return message;
      // 紧接在上一段文本后面就续写；否则新起一段（中间可能隔着工具调用）
      if (last && last.kind === "text") {
        const blocks = message.blocks.slice(0, -1);
        return { ...message, blocks: [...blocks, { kind: "text", text: last.text + delta }] };
      }
      return { ...message, blocks: [...message.blocks, { kind: "text", text: delta }] };
    }

    case "tool_call": {
      const block: Block = {
        kind: "tool",
        callId: event.id ?? "unknown",
        name: event.name ?? "未知工具",
        args: event.arguments ?? {},
        state: "running",
      };
      return { ...message, blocks: [...message.blocks, block] };
    }

    case "tool_result": {
      const index = findToolIndex(message.blocks, event.id);
      if (index < 0) return message;
      const block = message.blocks[index];
      if (block.kind !== "tool") return message;
      const updated: Block = {
        ...block,
        result: event.content ?? "",
        isError: Boolean(event.is_error),
        state: "done",
      };
      const blocks = [...message.blocks];
      blocks[index] = updated;
      return { ...message, blocks };
    }

    case "notice": {
      return {
        ...message,
        blocks: [...message.blocks, { kind: "notice", message: event.message ?? "" }],
      };
    }

    case "error": {
      return {
        ...message,
        blocks: [...message.blocks, { kind: "error", message: event.message ?? "未知错误" }],
      };
    }

    // done / usage / approval_request 不改变消息内容，分别由 state 的其它字段承载
    default:
      return message;
  }
}

function findToolIndex(blocks: Block[], callId: string | undefined): number {
  if (!callId) return -1;
  for (let i = blocks.length - 1; i >= 0; i -= 1) {
    const block = blocks[i];
    if (block.kind === "tool" && block.callId === callId) return i;
  }
  return -1;
}

export function reducer(state: ChatState, action: Action): ChatState {
  switch (action.type) {
    case "sessions":
      return { ...state, sessions: action.sessions };

    case "open":
      return {
        ...state,
        sessionId: action.sessionId,
        messages: action.messages,
        pending: [],
        todos: [],
        lastTurnTokens: 0,
        totalTokens: 0,
        error: null,
      };

    case "new-session":
      return {
        ...state,
        sessionId: null,
        messages: [],
        pending: [],
        todos: [],
        lastTurnTokens: 0,
        totalTokens: 0,
        error: null,
      };

    case "send":
      return {
        ...state,
        streaming: true,
        error: null,
        messages: [
          ...state.messages,
          { id: newId(), role: "user", blocks: [{ kind: "text", text: action.text }] },
          { id: action.assistantId, role: "assistant", blocks: [], streaming: true },
        ],
      };

    case "event": {
      const { event } = action;

      if (event.type === "approval_request") {
        if (!event.id) return state;
        // 同一个 call_id 不重复入队（重连或重复推送时会发生）
        if (state.pending.some((p) => p.callId === event.id)) return state;
        return {
          ...state,
          pending: [
            ...state.pending,
            {
              callId: event.id,
              name: event.name ?? "未知工具",
              args: event.arguments ?? {},
              reason: event.reason ?? "",
            },
          ],
        };
      }

      if (event.type === "usage") {
        const turn = event.total_tokens ?? 0;
        return {
          ...state,
          lastTurnTokens: turn,
          totalTokens: state.totalTokens + turn,
        };
      }

      // 清单和 usage 一样是会话级状态，不进消息流；快照整面替换
      if (event.type === "todo") {
        return { ...state, todos: event.items ?? [] };
      }

      return {
        ...state,
        messages: state.messages.map((m) =>
          m.id === action.assistantId ? applyEvent(m, event) : m,
        ),
      };
    }

    case "session-id":
      return { ...state, sessionId: action.sessionId };

    case "todos":
      return { ...state, todos: action.items };

    case "stream-end":
      return {
        ...state,
        streaming: false,
        pending: [],
        messages: state.messages.map((m) => (m.streaming ? { ...m, streaming: false } : m)),
      };

    case "approval":
      return { ...state, pending: state.pending.filter((p) => p.callId !== action.callId) };

    case "error":
      return { ...state, error: action.message, streaming: false };

    case "clear-error":
      return { ...state, error: null };

    default:
      return state;
  }
}
