import { useEffect, useMemo, useState } from "react";
import {
  fetchCallSnapshot,
  fetchConsoleOverview,
  fetchConsoleSession,
  fetchSessions,
  renameSession,
} from "../../api";
import { estimateCost, fmtTokens } from "../../cost";
import { Link } from "../../router";
import type {
  CallSnapshot,
  ConsoleOverview,
  SessionDetail,
  SessionSummary,
} from "../../types";
import { cacheSplit, finishLabel, fmtMs } from "./shared";

/**
 * 会话页：左边列表、右边详情。
 *
 * 为什么改成 master-detail（第 32 步）：
 * 上一版是「一张表 + 行内展开」，展开后整棵树把表格撑到三屏高，
 * 上下文全丢了，看第二轮还得往上翻回表头。业界工具（Langfuse / Phoenix /
 * LangSmith）在这个位置是同一个套路：**列表只负责选，详情独占一块区域**。
 *
 * 详情页本身借了三个约定：
 *  1. 顶部一条 metadata（名字 / id / 起止时间 / 规模），进来先知道"这是哪次对话"；
 *  2. 一排指标卡（tokens / 轮次 / 工具 / 缓存 / 估算花费），先看规模再看细节；
 *  3. 主体是**按时间排的时间线**，一轮一张卡：模型调用是节点，
 *     这一轮的工具调用挂在节点下面——顺序就是 agent 的真实思考顺序。
 */

/** ISO 时间 → 10-29 16:20。详情页里不需要年份，同一年的事看年份只会分心 */
function fmtTime(iso: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`;
}

export function SessionsPage() {
  const [overview, setOverview] = useState<ConsoleOverview | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    Promise.all([fetchConsoleOverview(), fetchSessions()])
      .then(([ov, list]) => {
        setOverview(ov);
        setSessions(list);
        // 深链优先（工具页的失败样本会带 ?session=xxx 跳过来），否则选第一个
        const target = new URLSearchParams(window.location.search).get("session");
        const first = target || list[0]?.session_id || null;
        if (first) void open(first);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function open(sessionId: string) {
    setSelected(sessionId);
    setDetail(null);
    setDetailError(null);
    try {
      setDetail(await fetchConsoleSession(sessionId));
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : String(err));
    }
  }

  function applyAlias(sessionId: string, alias: string) {
    setSessions((prev) => prev.map((s) => (s.session_id === sessionId ? { ...s, alias } : s)));
    setDetail((prev) => (prev && prev.session_id === sessionId ? { ...prev, alias } : prev));
  }

  /** 令牌数从总览里按会话号并进来：列表接口本身不带 token（那是另一张表的事） */
  const rows = useMemo(() => {
    const stats = new Map((overview?.by_session ?? []).map((s) => [s.session_id, s]));
    return sessions.map((s) => ({ ...s, stats: stats.get(s.session_id) }));
  }, [sessions, overview]);

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter(
      (r) =>
        r.session_id.includes(needle) ||
        (r.alias || "").toLowerCase().includes(needle) ||
        (r.title || "").toLowerCase().includes(needle),
    );
  }, [rows, filter]);

  if (error) return <p className="settings__msg settings__msg--err">加载失败：{error}</p>;
  if (!overview) return <p className="settings__note">加载中…</p>;

  return (
    <>
      <header className="page-head">
        <h1>会话分析</h1>
        <p>左侧选会话，右侧看这一场对话的完整轨迹：每轮模型调用 + 该轮工具链 + 请求原文</p>
      </header>

      <div className="sess">
        <aside className="sess__list panel">
          <input
            className="panel__filter sess__search"
            placeholder="搜别名、首句或会话号"
            value={filter}
            onChange={(ev) => setFilter(ev.target.value)}
          />

          {shown.length === 0 ? (
            <p className="settings__note">没有匹配的会话。</p>
          ) : (
            <ul className="sess__items">
              {shown.map((row) => {
                const name = row.alias || row.title || "（空会话）";
                const active = row.session_id === selected;
                return (
                  <li key={row.session_id}>
                    <button
                      className={`sessitem${active ? " sessitem--active" : ""}`}
                      onClick={() => void open(row.session_id)}
                      title={row.title || row.session_id}
                    >
                      <span className="sessitem__name">{name}</span>
                      {row.alias && <span className="sessitem__sub">{row.title}</span>}
                      <span className="sessitem__meta">
                        <span className="sessitem__id">{row.session_id.slice(0, 6)}</span>
                        <span>{fmtTime(row.updated_at)}</span>
                        <span>{row.messages} 条</span>
                        {row.stats && <span>{fmtTokens(row.stats.total_tokens)} tok</span>}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>

        <section className="sess__detail">
          {!selected && <p className="settings__note">从左侧选一个会话。</p>}
          {selected && detailError && (
            <p className="settings__msg settings__msg--err">加载失败：{detailError}</p>
          )}
          {selected && !detail && !detailError && <p className="settings__note">加载中…</p>}
          {detail && (
            <>
              <SessionHeader detail={detail} onAlias={applyAlias} />
              <SessionTimeline detail={detail} />
            </>
          )}
        </section>
      </div>
    </>
  );
}

/** 详情页顶部：名字（可改）/ 身份 / 起止时间 / 规模指标 */
function SessionHeader({
  detail,
  onAlias,
}: {
  detail: SessionDetail;
  onAlias: (sessionId: string, alias: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const name = detail.alias || detail.title || "（空会话）";
  const rounds = detail.llm_calls.length;
  const tokens = detail.llm_calls.reduce((sum, c) => sum + c.total_tokens, 0);
  const hit = detail.llm_calls.reduce((sum, c) => sum + c.cache_hit_tokens, 0);
  const miss = detail.llm_calls.reduce((sum, c) => sum + c.cache_miss_tokens, 0);
  const completion = detail.llm_calls.reduce((sum, c) => sum + c.completion_tokens, 0);
  const model = detail.llm_calls[0]?.model ?? "";
  const cost = model ? estimateCost(model, hit, miss, completion) : null;
  const { hitPct } = cacheSplit(hit, miss);
  const failed = detail.tool_calls.filter((t) => t.is_error).length;

  async function save() {
    setSaving(true);
    setErr(null);
    try {
      const result = await renameSession(detail.session_id, draft);
      onAlias(detail.session_id, result.alias);
      setEditing(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="sesshead panel">
      <div className="sesshead__top">
        {editing ? (
          <div className="sesshead__edit">
            <input
              autoFocus
              className="sesshead__input"
              value={draft}
              maxLength={60}
              placeholder="给这场对话起个名字，回车保存，留空恢复首句"
              onChange={(ev) => setDraft(ev.target.value)}
              onKeyDown={(ev) => {
                if (ev.key === "Enter") void save();
                if (ev.key === "Escape") setEditing(false);
              }}
            />
            <button className="btn btn--primary" disabled={saving} onClick={() => void save()}>
              {saving ? "保存中…" : "保存"}
            </button>
            <button className="btn btn--ghost" onClick={() => setEditing(false)}>
              取消
            </button>
          </div>
        ) : (
          <>
            <h2 className="sesshead__name">{name}</h2>
            <button
              className="pill pill--link"
              onClick={() => {
                setDraft(detail.alias);
                setEditing(true);
              }}
            >
              {detail.alias ? "改别名" : "起个别名"}
            </button>
            <Link
              className="pill pill--link"
              to={`/?session=${detail.session_id}`}
              title="回到工作台接着聊（同窗口切换，不刷新）"
            >
              在工作台打开
            </Link>
          </>
        )}
      </div>

      {err && <p className="settings__msg settings__msg--err">{err}</p>}

      <div className="sesshead__meta">
        <code>{detail.session_id}</code>
        <span>创建 {fmtTime(detail.created_at)}</span>
        <span>更新 {fmtTime(detail.updated_at)}</span>
        <span>{detail.messages} 条消息</span>
      </div>

      <div className="sesskpi">
        <div className="sesskpi__cell">
          <strong>{fmtTokens(tokens)}</strong>
          <span>tokens</span>
        </div>
        <div className="sesskpi__cell">
          <strong>{rounds}</strong>
          <span>轮模型调用</span>
        </div>
        <div className="sesskpi__cell">
          <strong className={failed > 0 ? "dtable__bad" : undefined}>
            {detail.tool_calls.length}
            {failed > 0 && <em> / 失败 {failed}</em>}
          </strong>
          <span>工具调用</span>
        </div>
        <div className="sesskpi__cell">
          <strong>{hitPct}%</strong>
          <span>输入缓存命中</span>
        </div>
        <div className="sesskpi__cell">
          <strong>{cost == null ? "—" : `¥${cost.toFixed(3)}`}</strong>
          <span>估算花费</span>
        </div>
      </div>
    </div>
  );
}

/** 时间线：一轮一张卡，工具挂在卡里。

 * 默认倒序（最新在上）：排查问题时 90% 的动作是"看刚才那轮发生了什么"，
 * 正序意味着每次都要滚到底；LangSmith Trajectories 和日志系统都是最新在前。
 * 保留正序切换——从头理解一次会话的因果链时正序更自然。
 */
function SessionTimeline({ detail }: { detail: SessionDetail }) {
  const [order, setOrder] = useState<"desc" | "asc">("desc");

  if (detail.llm_calls.length === 0) {
    return <p className="settings__note">这个会话还没有模型调用记录（可能只落了消息、没走到模型）。</p>;
  }

  // 老数据没有轮次号（全是 0）：时间线仍然能用，只是"第几轮"没意义
  const legacy = detail.llm_calls.every((c) => c.round_no === 0);

  // 先按时间正序组装（工具归属、looseTools 的"最后一个"都依赖正序），最后再决定展示方向
  const rounds = detail.llm_calls.map((call, index) => ({
    call,
    index,
    tools: legacy ? [] : detail.tool_calls.filter((t) => t.round_no === call.round_no),
    looseTools:
      legacy && index === detail.llm_calls.length - 1 ? detail.tool_calls : ([] as typeof detail.tool_calls),
  }));
  const shown = order === "desc" ? [...rounds].reverse() : rounds;

  return (
    <div className="timeline">
      <div className="timeline__bar">
        <span className="timeline__count">
          {rounds.length} 轮模型调用{legacy ? " · 老数据无轮次号" : ""}
        </span>
        <div className="timeline__toggle">
          <button
            className={`timeline__opt${order === "desc" ? " is-active" : ""}`}
            onClick={() => setOrder("desc")}
          >
            最新在前
          </button>
          <button
            className={`timeline__opt${order === "asc" ? " is-active" : ""}`}
            onClick={() => setOrder("asc")}
          >
            最早在前
          </button>
        </div>
      </div>
      {shown.map((round) => (
        <RoundCard key={round.call.id} call={round.call} index={round.index} tools={round.tools} looseTools={round.looseTools} />
      ))}
    </div>
  );
}

function RoundCard({
  call,
  index,
  tools,
  looseTools,
}: {
  call: SessionDetail["llm_calls"][number];
  index: number;
  tools: SessionDetail["tool_calls"];
  looseTools: SessionDetail["tool_calls"];
}) {
  const [open, setOpen] = useState(false);
  const [snapshot, setSnapshot] = useState<CallSnapshot | null>(null);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);

  const fin = finishLabel(call.finish_reason);
  const { hitPct, total: cacheable } = cacheSplit(call.cache_hit_tokens, call.cache_miss_tokens);
  const failed = tools.filter((t) => t.is_error).length;

  async function toggle() {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (snapshot) return;
    try {
      setSnapshot(await fetchCallSnapshot(call.id));
    } catch (err) {
      setSnapshotError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <article className={`tlitem${failed > 0 ? " tlitem--err" : ""}`}>
      <div className="tlitem__rail">
        <span className="tlitem__dot">{call.round_no || index + 1}</span>
        <span className="tlitem__line" />
      </div>

      <div className="tlitem__card">
        {call.last_user_text && (
          <p className="tlitem__user" title="这一轮用户的问题（从请求快照解析）">
            用户：{call.last_user_text}
          </p>
        )}
        <header className="tlitem__head">
          <span className="tlitem__round">第 {call.round_no || index + 1} 轮</span>
          <code className="tlitem__model">{call.model}</code>
          <span className="tlitem__chip">{fmtTokens(call.total_tokens)} tok</span>
          {cacheable > 0 && (
            <span className="tlitem__chip" title={`命中 ${call.cache_hit_tokens} / 未命中 ${call.cache_miss_tokens}`}>
              <span className="cachebar">
                <span className="cachebar__hit" style={{ width: `${hitPct}%` }} />
              </span>
              缓存 {hitPct}%
            </span>
          )}
          <span className="tlitem__chip">{fmtMs(call.duration_ms)}</span>
          <span className="tlitem__chip" title="首字节延迟：模型开始输出第一个 token 的时间">
            首字 {fmtMs(call.first_token_ms)}
          </span>
          {call.reasoning_tokens > 0 && (
            <span className="tlitem__chip">思考 {fmtTokens(call.reasoning_tokens)}</span>
          )}
          <span className={fin.warn ? "tlitem__fin tlitem__fin--warn" : "tlitem__fin"}>{fin.text}</span>
          <button className="pill" onClick={() => void toggle()}>
            {open ? "收起请求" : "看请求"}
          </button>
        </header>

        <div className="tlitem__sub">
          <span>{call.req_messages} 条消息</span>
          <span>·</span>
          <span>{call.req_tools} 个工具定义</span>
          <span>·</span>
          <span>输出 {fmtTokens(call.completion_tokens)} tok</span>
          <span>·</span>
          <span>{fmtTime(call.created_at)}</span>
        </div>

        {tools.length > 0 && (
          <ul className="tltools">
            {tools.map((tool, i) => (
              <li
                key={`${tool.name}-${i}`}
                className={`tltool${tool.is_error ? " tltool--err" : ""}`}
                title={tool.error_excerpt || undefined}
              >
                <span className="tltool__name">{tool.name}</span>
                <span className="tltool__meta">{fmtMs(tool.duration_ms)}</span>
                {tool.is_error && (
                  <span className="tltool__err">{tool.error_excerpt || "失败（无详情）"}</span>
                )}
              </li>
            ))}
          </ul>
        )}

        {tools.length === 0 && (
          <p className="tlitem__empty">本轮没有工具调用（模型直接收尾）</p>
        )}

        {looseTools.length > 0 && (
          <ul className="tltools">
            {looseTools.map((tool, i) => (
              <li
                key={`loose-${i}`}
                className={`tltool${tool.is_error ? " tltool--err" : ""}`}
                title={tool.error_excerpt || undefined}
              >
                <span className="tltool__name">{tool.name}</span>
                <span className="tltool__meta">{fmtMs(tool.duration_ms)}</span>
                {tool.is_error && <span className="tltool__err">{tool.error_excerpt || "失败"}</span>}
              </li>
            ))}
          </ul>
        )}

        {open && (
          <div className="tlitem__snap">
            {snapshotError && <p className="settings__msg settings__msg--err">{snapshotError}</p>}
            {!snapshotError && !snapshot && <p className="settings__note">加载中…</p>}
            {snapshot && <SnapshotView snapshot={snapshot} />}
          </div>
        )}
      </div>
    </article>
  );
}

/**
 * 请求快照。
 *
 * 后端存的是 messages 数组（超 4KB 时是 {truncated, head, tail} 信封）。
 * 这里不做语法高亮——查问题时看的是"某条消息里写了什么"，
 * 原始 JSON 加等宽字体已经够用，加高亮只会让复制变难。
 */
function SnapshotView({ snapshot }: { snapshot: CallSnapshot }) {
  const raw = snapshot.snapshot;
  if (raw == null) {
    return <p className="settings__note">这条记录没有快照（第 31 步之前的老数据）。</p>;
  }
  const isEnvelope =
    typeof raw === "object" && raw !== null && "truncated" in (raw as Record<string, unknown>);

  if (isEnvelope) {
    const env = raw as { head: string; tail: string };
    return (
      <>
        <p className="settings__note">请求体超过 4KB，只保留开头与结尾：</p>
        <pre className="snapview__json">
          {env.head}
          {"\n……（中间已省略）……\n"}
          {env.tail}
        </pre>
      </>
    );
  }

  return (
    <>
      <p className="settings__note">
        {snapshot.req_messages} 条消息 · {snapshot.req_tools} 个工具定义 · 模型 {snapshot.model}
      </p>
      <pre className="snapview__json">{JSON.stringify(raw, null, 2)}</pre>
    </>
  );
}
