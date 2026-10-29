import { useEffect, useState } from "react";
import { fetchCallSnapshot, fetchConsoleCalls } from "../../api";
import { fmtTokens } from "../../cost";
import type { CallSnapshot, LlmCallRow } from "../../types";
import { cacheSplit, finishLabel, fmtMs } from "./shared";

/**
 * 调用流水页：每一次模型调用一行，回答「钱花在哪、请求发了什么」。
 * 点「请求」展开该次调用的请求体快照（messages JSON，落库时已截 4KB）。
 */
export function CallsPage() {
  const [calls, setCalls] = useState<LlmCallRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sessionFilter, setSessionFilter] = useState("");
  /** 当前展开快照的调用 id；null = 没展开 */
  const [openId, setOpenId] = useState<number | null>(null);
  const [snapshot, setSnapshot] = useState<CallSnapshot | null>(null);
  const [snapLoading, setSnapLoading] = useState(false);

  async function load(sessionId?: string) {
    setError(null);
    try {
      setCalls(await fetchConsoleCalls(100, sessionId || undefined));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function toggleSnapshot(id: number) {
    if (openId === id) {
      setOpenId(null);
      setSnapshot(null);
      return;
    }
    setOpenId(id);
    setSnapshot(null);
    setSnapLoading(true);
    try {
      setSnapshot(await fetchCallSnapshot(id));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSnapLoading(false);
    }
  }

  return (
    <>
      <header className="admin__page-head">
        <h1>调用流水</h1>
        <p>最近 100 次模型调用 · 输入拆成命中/未命中两桶 · 点「请求」看该次到底发了什么</p>
      </header>

      <section className="panel">
        <div className="panel__toolbar">
          <input
            className="panel__filter"
            placeholder="按会话号过滤，如 a1b2c3d4e5f6"
            value={sessionFilter}
            onChange={(e) => setSessionFilter(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void load(sessionFilter.trim());
            }}
          />
          <button className="pill" onClick={() => void load(sessionFilter.trim())}>
            过滤
          </button>
          <button
            className="pill"
            onClick={() => {
              setSessionFilter("");
              void load();
            }}
          >
            重置
          </button>
        </div>

        {error && <p className="settings__msg settings__msg--err">{error}</p>}
        {!calls && !error && <p className="settings__note">加载中…</p>}
        {calls && calls.length === 0 && <p className="settings__note">还没有调用记录。</p>}
        {calls && calls.length > 0 && (
          <table className="dtable">
            <thead>
              <tr>
                <th>时间</th>
                <th>会话</th>
                <th>轮</th>
                <th>模型</th>
                <th>输入(命中)</th>
                <th>输入(未命中)</th>
                <th>思考</th>
                <th>输出</th>
                <th>耗时</th>
                <th>首字</th>
                <th>结束原因</th>
                <th>消息数</th>
                <th>请求</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => {
                const fin = finishLabel(c.finish_reason);
                return (
                  <FragmentRow
                    key={c.id}
                    call={c}
                    finText={fin.text}
                    finWarn={fin.warn}
                    open={openId === c.id}
                    snapshot={openId === c.id ? snapshot : null}
                    snapLoading={snapLoading}
                    onToggle={() => void toggleSnapshot(c.id)}
                  />
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}

/** 一行 + 可选的展开行。<tr> 不能嵌套，展开行用 colSpan 整行铺满 */
function FragmentRow({
  call,
  finText,
  finWarn,
  open,
  snapshot,
  snapLoading,
  onToggle,
}: {
  call: LlmCallRow;
  finText: string;
  finWarn: boolean;
  open: boolean;
  snapshot: CallSnapshot | null;
  snapLoading: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr>
        <td>{call.created_at.replace("T", " ").slice(5, 16)}</td>
        <td>
          <code>{call.session_id}</code>
        </td>
        <td>{call.round_no}</td>
        <td>
          <code>{call.model}</code>
        </td>
        <td className="dtable__good">{fmtTokens(call.cache_hit_tokens)}</td>
        <td>{fmtTokens(call.cache_miss_tokens)}</td>
        <td>{call.reasoning_tokens ? fmtTokens(call.reasoning_tokens) : "—"}</td>
        <td>{fmtTokens(call.completion_tokens)}</td>
        <td>{fmtMs(call.duration_ms)}</td>
        <td>{fmtMs(call.first_token_ms)}</td>
        <td className={finWarn ? "dtable__bad" : undefined}>{finText}</td>
        <td>{call.req_messages}</td>
        <td>
          <button className="pill" onClick={onToggle}>
            {open ? "收起" : "查看"}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="dtable__expand">
          <td colSpan={13}>
            {snapLoading && <p className="settings__note">加载快照…</p>}
            {snapshot && <SnapshotBody snapshot={snapshot} call={call} />}
          </td>
        </tr>
      )}
    </>
  );
}

/** 快照正文：结构化拆分布 + 原始 JSON（截断的显示头尾） */
function SnapshotBody({ snapshot, call }: { snapshot: CallSnapshot; call: LlmCallRow }) {
  const ctx = [
    ["system", call.ctx_system],
    ["history", call.ctx_history],
    ["question", call.ctx_question],
    ["tool_results", call.ctx_tool_results],
  ] as const;
  const snap = snapshot.snapshot;
  const truncated =
    typeof snap === "object" && snap !== null && "truncated" in (snap as Record<string, unknown>);

  return (
    <div className="snapview">
      <div className="snapview__meta">
        <span>
          消息 {snapshot.req_messages} 条 · 工具定义 {snapshot.req_tools} 个
        </span>
        <span>
          上下文构成（字符）：{ctx.map(([k, v]) => `${k}=${fmtTokens(v)}`).join(" / ")}
        </span>
        <span>
          缓存分布：
          <span className="cachebar">
            <span
              className="cachebar__hit"
              style={{ width: `${cacheSplit(call.cache_hit_tokens, call.cache_miss_tokens).hitPct}%` }}
            />
          </span>
          {cacheSplit(call.cache_hit_tokens, call.cache_miss_tokens).hitPct}% 命中
        </span>
      </div>
      {truncated ? (
        <>
          <p className="settings__note">快照超过 4KB 已截断，以下为头尾片段：</p>
          <pre className="snapview__json">{String((snap as { head: string }).head)}</pre>
          <pre className="snapview__json">{String((snap as { tail: string }).tail)}</pre>
        </>
      ) : (
        <pre className="snapview__json">
          {snap == null ? "（无快照——老数据或序列化失败）" : JSON.stringify(snap, null, 2)}
        </pre>
      )}
    </div>
  );
}
