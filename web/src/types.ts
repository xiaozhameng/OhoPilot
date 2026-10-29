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

/** 审批时可以选的一个授权范围（第 32 步）。
 *  once = 仅本次；file = 这个文件；dir = 这个目录及子目录。 */
export interface ApprovalOption {
  kind: "once" | "file" | "dir";
  label: string;
  path: string;
}

/** 后端推送的原始事件：type 加扁平的字段。缺哪个字段取决于 type。 */
export interface RawEvent {
  type: EventType;
  delta?: string;
  id?: string;
  name?: string;
  arguments?: Record<string, unknown>;
  /** approval_request 专属：允许时可选的授权范围 */
  options?: ApprovalOption[];
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
    /** 缓存命中/未命中的输入 token（控制台分析化后新记；老数据为 0） */
    cache_hit_tokens: number;
    cache_miss_tokens: number;
    /** 思考模型的隐藏输出（DeepSeek-R1 / o 系列） */
    reasoning_tokens: number;
  }>;
  tool_stats: Array<{
    name: string;
    calls: number;
    errors: number;
    /** 平均/最大耗时（毫秒）。没有真正执行过的调用为 null */
    avg_ms: number | null;
    max_ms: number | null;
  }>;
  tool_calls_total: number;
  tool_errors_total: number;
  by_session: Array<{
    session_id: string;
    /** 用户起的别名（空串 = 没起过，界面回退到 title） */
    alias: string;
    /** 首句用户提问，天生就是最直观的标题 */
    title: string;
    /** 会话首条 / 最后一条消息时间（ISO 字符串） */
    created_at: string;
    updated_at: string;
    llm_calls: number;
    total_tokens: number;
    prompt_tokens: number;
    completion_tokens: number;
    tool_calls: number;
  }>;
  /** 近 14 天趋势（第 29 步）：空日子也有一行，方便画图 */
  trend: Array<{ date: string; llm_calls: number; tokens: number; tool_calls: number }>;
  /** 最近的工具失败样本：报错原文前 200 字，控制台排障第一入口 */
  error_samples: Array<{
    session_id: string;
    created_at: string;
    name: string;
    excerpt: string | null;
  }>;
}

/** 一次模型调用的流水行（GET /console/calls）。 */
export interface LlmCallRow {
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
  finish_reason: string;
  reasoning_tokens: number;
  req_messages: number;
  req_tools: number;
  ctx_system: number;
  ctx_history: number;
  ctx_question: number;
  ctx_tool_results: number;
}

/** 单次调用的请求快照（GET /console/llm-calls/{id}）。snapshot 是 messages JSON。 */
export interface CallSnapshot {
  id: number;
  created_at: string;
  model: string;
  req_messages: number;
  req_tools: number;
  snapshot: unknown | { truncated: boolean; head: string; tail: string } | null;
}

/** 控制台会话下钻：GET /console/api/sessions/{id} 的返回结构（带轮次） */
export interface SessionDetail {
  session_id: string;
  /** 别名 / 首句标题 / 首末时间 / 消息条数（第 32 步） */
  alias: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: number;
  llm_calls: Array<{
    id: number;
    round_no: number;
    created_at: string;
    model: string;
    prompt_tokens: number;
    cache_hit_tokens: number;
    cache_miss_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    duration_ms: number;
    first_token_ms: number;
    finish_reason: string;
    reasoning_tokens: number;
    req_messages: number;
    req_tools: number;
    /** 这一轮用户问的是什么（后端从快照解析的 160 字摘要；老数据/截断快照为空） */
    last_user_text: string;
  }>;
  tool_calls: Array<{
    round_no: number;
    created_at: string;
    name: string;
    is_error: boolean;
    duration_ms: number | null;
    error_excerpt: string | null;
  }>;
}

/** 扩展中心：技能与 MCP（GET /skills、GET /mcp 等） */
export interface SkillInfo {
  name: string;
  description: string;
  file: string;
}

/** 一次安全审计的发现。severity: danger=拒装 / warning=需确认 */
export interface AuditFinding {
  severity: "danger" | "warning";
  label: string;
  line_no: number;
  excerpt: string;
}

export interface SkillInstallResult {
  ok: boolean;
  message: string;
  findings: AuditFinding[];
}

/** GitHub 仓库里发现的一个技能候选 */
export interface SkillCandidate {
  name: string;
  path: string;
  raw_url: string;
}

/** mcp.json 里一个 server 的原始配置（Claude Desktop 同格式） */
export interface McpServer {
  command: string;
  args?: string[];
  env?: Record<string, string>;
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
  /** 用户起的别名；空串表示没起，展示 title */
  alias: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: number;
  /** 所属空间（第 34 步）。老数据后端兜 1 = 默认空间 */
  space_id: number;
}

/** 空间：会话的一级分组，可绑定一个本地文件夹（元数据）。 */
export interface Space {
  id: number;
  name: string;
  /** 绑定的本地目录；空串 = 没绑 */
  folder: string;
  created_at: string;
  /** 该空间下的会话数 */
  sessions: number;
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
  /** 允许时可以选的范围；缺省表示只能允许/拒绝 */
  options: ApprovalOption[];
}

/** 一条目录白名单（GET /console/api/grants）。 */
export interface DirectoryGrant {
  id: string;
  tool: string;
  path: string;
  kind: "file" | "dir";
  created_at: string;
  label: string;
}

/** 长期记忆文件（GET/PUT /console/api/memory）。 */
export interface MemoryFile {
  /** 落盘位置，用户想手改时能找到 */
  path: string;
  content: string;
  /** 文件总字符数 */
  chars: number;
  /** 去掉注释、截断后实际会注入系统提示的字符数 */
  injected_chars: number;
  /** 注入上限（超出部分模型看不到） */
  limit: number;
  file_limit: number;
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
