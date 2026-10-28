import { useEffect, useState } from "react";
import {
  fetchConsoleOverview,
  fetchConsoleSession,
} from "../api";
import { estimateCost, fmtTokens } from "../cost";
import type { ConsoleOverview, SessionDetail } from "../types";

/**
 * 开发者控制台：跨会话的聚合视图，和主工作台是同一个 SPA 的两个入口。
 * 主界面看「这一次对话」，这里看「这个部署一直以来的样子」——
 * 定位是运维/优化视角：哪个模型花钱多、哪个工具慢、失败的原因是什么。
 */
export function ConsolePage() {
  const [data, setData] = useState<ConsoleOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** 当前展开下钻的会话号；null = 没有展开任何会话 */
  const [drillId, setDrillId] = useState<string | null>(null);
  const [drill, setDrill] = useState<SessionDetail | null>(null);
  const [drillError, setDrillError] = useState<string | null>(null);

  useEffect(() => {
    fetchConsoleOverview()
      .then(setData)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  /** 点会话行：展开就拉明细，再点收起。明细不缓存，每次都现拉（数据会变旧） */
  async function toggleDrill(sessionId: string) {
    if (drillId === sessionId) {
      setDrillId(null);
      setDrill(null);
      return;
    }
    setDrillId(sessionId);
    setDrill(null);
    setDrillError(null);
    try {
      setDrill(await fetchConsoleSession(sessionId));
    } catch (err: unknown) {
      setDrillError(err instanceof Error ? err.message : String(err));
    }
  }

  if (error) {
    return (
      <main className="console">
        <p className="settings__msg settings__msg--err">加载失败：{error}</p>
      </main>
    );
  }
  if (!data) {
    return (
      <main className="console">
        <p className="settings__note">加载中…</p>
      </main>
    );
  }

  const usage = data.usage;
  const hitPct = Math.round(usage.cache_hit_rate * 1000) / 10;
  const toolErrorPct =
    data.tool_calls_total > 0
      ? Math.round((data.tool_errors_total / data.tool_calls_total) * 1000) / 10
      : 0;
  // 趋势图的标尺：最高那天 tokens。0 会除零，兜成 1
  const maxDayTokens = Math.max(1, ...data.trend.map((d) => d.tokens));

  function fmtMs(ms: number | null): string {
    if (ms == null) return "—";
    if (ms >= 10_000) return `${(ms / 1000).toFixed(0)}s`;
    if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
    return `${ms}ms`;
  }

  return (
    <main className="console">
      <header className="console__head">
        <div>
          <h1 className="console__title">开发者控制台</h1>
          <p className="console__sub">跨会话聚合 · 单用户阶段无鉴权，不要暴露到公网</p>
        </div>
        <a className="pill pill--link" href="/">
          ← 返回工作台
        </a>
      </header>

      <section className="usage__cards">
        <div className="usage__card">
          <span className="usage__num">{usage.calls}</span>
          <span className="usage__label">模型调用次数</span>
        </div>
        <div className="usage__card">
          <span className="usage__num">{fmtTokens(usage.total_tokens)}</span>
          <span className="usage__label">总 tokens</span>
        </div>
        <div className="usage__card">
          <span className={`usage__num${usage.calls && hitPct < 50 ? " usage__num--warn" : ""}`}>
            {usage.calls ? `${hitPct}%` : "—"}
          </span>
          <span className="usage__label">缓存命中率</span>
        </div>
        <div className="usage__card">
          <span className="usage__num">
            {usage.avg_first_token_ms ? `${(usage.avg_first_token_ms / 1000).toFixed(1)}s` : "—"}
          </span>
          <span className="usage__label">平均首字延迟</span>
        </div>
        <div className="usage__card">
          <span className="usage__num">{data.tool_calls_total}</span>
          <span className="usage__label">工具调用次数</span>
        </div>
        <div className="usage__card">
          <span className={`usage__num${toolErrorPct > 10 ? " usage__num--warn" : ""}`}>
            {data.tool_calls_total ? `${toolErrorPct}%` : "—"}
          </span>
          <span className="usage__label">工具错误率</span>
        </div>
      </section>

      <section className="console__section">
        <h2>近 14 天趋势</h2>
        {data.trend.length === 0 ? (
          <p className="settings__note">还没有调用记录。</p>
        ) : (
          <div className="trend">
            {data.trend.map((d) => (
              <div key={d.date} className="trend__col" title={`${d.date}：${fmtTokens(d.tokens)} tokens / ${d.llm_calls} 次模型调用 / ${d.tool_calls} 次工具`}>
                <div className="trend__bar-wrap">
                  <div
                    className="trend__bar"
                    style={{ height: `${Math.max(2, (d.tokens / maxDayTokens) * 100)}%` }}
                  />
                </div>
                <span className="trend__label">{d.date.slice(5)}</span>
              </div>
            ))}
          </div>
        )}
        <p className="settings__note">
          柱高按当天 tokens 占比。悬停看当天明细。模型调用集中在个别日子 = 用得少；
          每天 tokens 都很高 = 上下文偏大，优先看缓存命中率。
        </p>
      </section>

      <section className="console__section">
        <h2>按模型拆分</h2>
        {data.by_model.length === 0 ? (
          <p className="settings__note">还没有调用记录。</p>
        ) : (
          <table className="usage__table">
            <thead>
              <tr>
                <th>模型</th>
                <th>调用</th>
                <th>输入 tokens</th>
                <th>输出 tokens</th>
                <th>合计</th>
                <th>约 ¥</th>
              </tr>
            </thead>
            <tbody>
              {data.by_model.map((m) => (
                <tr key={m.model}>
                  <td>
                    <code>{m.model}</code>
                  </td>
                  <td>{m.calls}</td>
                  <td>{fmtTokens(m.prompt_tokens)}</td>
                  <td>{fmtTokens(m.completion_tokens)}</td>
                  <td>{fmtTokens(m.total_tokens)}</td>
                  <td>
                    {/* 模型路由（第 25 步）的效果就在这张表里：
                        fast_model 的行单价低，占比涨上来 = 路由在省钱 */}
                    {estimateCost(
                      m.model,
                      m.prompt_tokens * 0.3,
                      m.prompt_tokens * 0.7,
                      m.completion_tokens,
                    )?.toFixed(4) ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="settings__note">
          ¥ 列按价格表估算（价格表在前端 cost.ts）。控制台没有逐条缓存命中数据，
          按 30% 命中 / 70% 未命中近似——要看精确值用主界面的用量抽屉。
        </p>
      </section>

      <section className="console__section">
        <h2>工具耗时与失败排行</h2>
        {data.tool_stats.length === 0 ? (
          <p className="settings__note">还没有工具调用。</p>
        ) : (
          <table className="usage__table">
            <thead>
              <tr>
                <th>工具</th>
                <th>调用</th>
                <th>失败</th>
                <th>平均耗时</th>
                <th>最大耗时</th>
              </tr>
            </thead>
            <tbody>
              {data.tool_stats.map((t) => (
                <tr key={t.name}>
                  <td>
                    <code>{t.name}</code>
                  </td>
                  <td>{t.calls}</td>
                  <td className={t.errors > 0 ? "usage__hit" : undefined}>{t.errors}</td>
                  <td>{fmtMs(t.avg_ms)}</td>
                  <td>{fmtMs(t.max_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="settings__note">
          平均耗时高 + 失败多 = 该加超时或缓存；最大耗时远超平均 = 偶发卡死，
          值得查那次调用的会话明细。
        </p>
      </section>

      <section className="console__section">
        <h2>最近失败样本</h2>
        {data.error_samples.length === 0 ? (
          <p className="settings__note">没有失败记录。</p>
        ) : (
          <ul className="errlist">
            {data.error_samples.map((e, i) => (
              <li key={i} className="errlist__item">
                <div className="errlist__meta">
                  <code>{e.name}</code>
                  <span>{e.created_at.replace("T", " ")}</span>
                  <a
                    className="pill pill--link"
                    href={`/?session=${e.session_id}`}
                    onClick={(ev) => {
                      ev.preventDefault();
                      void toggleDrill(e.session_id);
                    }}
                  >
                    会话 {e.session_id}
                  </a>
                </div>
                <pre className="errlist__excerpt">{e.excerpt || "（无错误信息）"}</pre>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="console__section">
        <h2>会话成本排行（Top 20）</h2>
        {data.by_session.length === 0 ? (
          <p className="settings__note">还没有会话。</p>
        ) : (
          <table className="usage__table">
            <thead>
              <tr>
                <th>会话</th>
                <th>模型调用</th>
                <th>总 tokens</th>
              </tr>
            </thead>
            <tbody>
              {data.by_session.map((s) => (
                <tr key={s.session_id}>
                  <td>
                    <a
                      className="pill pill--link"
                      href={`/?session=${s.session_id}`}
                      onClick={(ev) => {
                        ev.preventDefault();
                        void toggleDrill(s.session_id);
                      }}
                    >
                      {s.session_id}
                    </a>
                  </td>
                  <td>{s.llm_calls}</td>
                  <td>{fmtTokens(s.total_tokens)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="settings__note">点会话号在本页展开逐轮明细；要去对话原文用主界面打开。</p>

        {drillId && (
          <div className="drill">
            <div className="drill__head">
              <strong>会话 {drillId}</strong>
              <button className="pill" onClick={() => void toggleDrill(drillId)}>
                收起
              </button>
            </div>
            {drillError && <p className="settings__msg settings__msg--err">{drillError}</p>}
            {!drill && !drillError && <p className="settings__note">加载中…</p>}
            {drill && (
              <>
                <h3 className="drill__sub">逐轮模型调用</h3>
                <table className="usage__table">
                  <thead>
                    <tr>
                      <th>轮</th>
                      <th>时间</th>
                      <th>模型</th>
                      <th>总 tokens</th>
                      <th>耗时</th>
                      <th>首字</th>
                    </tr>
                  </thead>
                  <tbody>
                    {drill.llm_calls.map((c, i) => (
                      <tr key={i}>
                        <td>{c.round_no}</td>
                        <td>{c.created_at.replace("T", " ").slice(5, 16)}</td>
                        <td>
                          <code>{c.model}</code>
                        </td>
                        <td>{fmtTokens(c.total_tokens)}</td>
                        <td>{fmtMs(c.duration_ms || null)}</td>
                        <td>{fmtMs(c.first_token_ms || null)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <h3 className="drill__sub">工具调用</h3>
                {drill.tool_calls.length === 0 ? (
                  <p className="settings__note">这个会话没有调过工具。</p>
                ) : (
                  <table className="usage__table">
                    <thead>
                      <tr>
                        <th>时间</th>
                        <th>工具</th>
                        <th>结果</th>
                        <th>耗时</th>
                      </tr>
                    </thead>
                    <tbody>
                      {drill.tool_calls.map((t, i) => (
                        <tr key={i}>
                          <td>{t.created_at.replace("T", " ").slice(5, 16)}</td>
                          <td>
                            <code>{t.name}</code>
                          </td>
                          <td className={t.is_error ? "usage__hit" : undefined}>
                            {t.is_error ? t.error_excerpt || "失败" : "成功"}
                          </td>
                          <td>{fmtMs(t.duration_ms)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}
          </div>
        )}
      </section>
    </main>
  );
}
