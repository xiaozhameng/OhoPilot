/**
 * 后端 HTTP 客户端。
 *
 * 最要紧的一点：**不能用 EventSource**。
 * 浏览器内置的 EventSource 只能发 GET、不能带请求体，而我们要 POST 一个 JSON 进去。
 * 所以这里用 fetch + ReadableStream 手写 SSE 解析——协议本身很简单：
 * 一个事件 = 若干行 `data: xxx`，以空行（\n\n）结束。
 */

import type {
  Health,
  HistoryMessage,
  RawEvent,
  SessionSummary,
  TodoItem,
  UsageCall,
  UsageSummary,
} from "./types";

/** 开发时 vite proxy 会把 /chat 转到 8737；生产时同源，所以一律用相对路径。 */
const BASE = "";

async function readError(resp: Response): Promise<string> {
  const text = await resp.text();
  try {
    const body = JSON.parse(text) as { detail?: string };
    if (body.detail) return body.detail;
  } catch {
    /* 不是 JSON 就用原文 */
  }
  return text || `HTTP ${resp.status}`;
}

export async function fetchHealth(): Promise<Health> {
  const resp = await fetch(`${BASE}/health`);
  if (!resp.ok) throw new Error(await readError(resp));
  return (await resp.json()) as Health;
}

export async function fetchSessions(): Promise<SessionSummary[]> {
  const resp = await fetch(`${BASE}/sessions`);
  if (!resp.ok) throw new Error(await readError(resp));
  const body = (await resp.json()) as { sessions: SessionSummary[] };
  return body.sessions;
}

export async function fetchSessionMessages(sessionId: string): Promise<HistoryMessage[]> {
  const resp = await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}`);
  if (resp.status === 404) return [];
  if (!resp.ok) throw new Error(await readError(resp));
  const body = (await resp.json()) as { messages: HistoryMessage[] };
  return body.messages;
}

/** 恢复某会话的任务清单。404（会话不存在/没有清单）按空清单处理。 */
export async function fetchSessionTodos(sessionId: string): Promise<TodoItem[]> {
  const resp = await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}/todos`);
  if (resp.status === 404) return [];
  if (!resp.ok) throw new Error(await readError(resp));
  const body = (await resp.json()) as { items: TodoItem[] };
  return body.items;
}

export async function answerApproval(callId: string, allow: boolean): Promise<void> {
  const resp = await fetch(`${BASE}/approvals/${encodeURIComponent(callId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ allow }),
  });
  // 404 表示这次审批已经超时或被答过，不算致命错误：界面照旧收起卡片即可
  if (!resp.ok && resp.status !== 404) throw new Error(await readError(resp));
}

/** 上传文件到服务端 uploads/，返回工作目录内的相对路径。 */
export async function uploadFile(file: File): Promise<string> {
  const form = new FormData();
  form.append("file", file);
  const resp = await fetch(`${BASE}/files`, { method: "POST", body: form });
  if (!resp.ok) throw new Error(await readError(resp));
  const body = (await resp.json()) as { path: string };
  return body.path;
}

export async function saveSettings(input: {
  model?: string;
  workspace?: string;
}): Promise<Health> {
  const resp = await fetch(`${BASE}/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!resp.ok) throw new Error(await readError(resp));
  return (await resp.json()) as Health;
}

export async function fetchUsage(
  sessionId: string | null,
): Promise<{ summary: UsageSummary; calls: UsageCall[] }> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  const resp = await fetch(`${BASE}/usage${query}`);
  if (!resp.ok) throw new Error(await readError(resp));
  return (await resp.json()) as { summary: UsageSummary; calls: UsageCall[] };
}

export interface AskOptions {
  message: string;
  sessionId: string | null;
  onEvent: (event: RawEvent) => void;
  /** 拿到响应头就会回调一次：新会话的 id 只在响应头里（body 是流，没法等它结束再取） */
  onSession: (sessionId: string) => void;
  signal?: AbortSignal;
}

/**
 * 发一轮问题，边收边解析。
 *
 * 返回 Promise，流结束（或客户端 abort）时 resolve。
 * 出错时抛异常，由调用方转成界面上的错误块——不在这里直接写 DOM。
 */
export async function ask(options: AskOptions): Promise<void> {
  const { message, sessionId, onEvent, onSession, signal } = options;

  const resp = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal,
  });

  if (!resp.ok) throw new Error(await readError(resp));
  if (!resp.body) throw new Error("响应没有可读的流（浏览器或代理不支持流式）");

  const newSessionId = resp.headers.get("X-Session-Id");
  if (newSessionId) onSession(newSessionId);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    // stream: true —— 一个 UTF-8 汉字可能被切在两个 chunk 里，必须让 decoder 自己拼
    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const chunk = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      handleChunk(chunk, onEvent);
      boundary = buffer.indexOf("\n\n");
    }
  }

  // 收尾：最后一个事件后面可能没有空行
  if (buffer.trim()) handleChunk(buffer, onEvent);
}

function handleChunk(chunk: string, onEvent: (event: RawEvent) => void): void {
  for (const line of chunk.split("\n")) {
    if (!line.startsWith("data:")) continue;
    const payload = line.slice(5).trim();
    if (!payload) continue;
    try {
      onEvent(JSON.parse(payload) as RawEvent);
    } catch {
      // 半条 JSON 不该让整个对话挂掉；真丢了事件，界面上会表现为停在「思考中」
      console.warn("无法解析的 SSE 数据", payload);
    }
  }
}
