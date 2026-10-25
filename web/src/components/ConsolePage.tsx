import { useEffect, useState } from "react";
import { fetchConsoleOverview } from "../api";
import { estimateCost, fmtTokens } from "../cost";
import type { ConsoleOverview } from "../types";

/**
 * 开发者控制台：跨会话的聚合视图，和主工作台是同一个 SPA 的两个入口。
 * 主界面看「这一次对话」，这里看「这个部署一直以来的样子」——
 * 定位是运维/优化视角：哪个模型花钱多、哪个工具失败多、哪个会话烧 token。
 */
export function ConsolePage() {
  const [data, setData] = useState<ConsoleOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchConsoleOverview()
      .then(setData)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

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
        <h2>工具调用排行</h2>
        {data.tool_stats.length === 0 ? (
          <p className="settings__note">还没有工具调用。</p>
        ) : (
          <table className="usage__table">
            <thead>
              <tr>
                <th>工具</th>
                <th>调用</th>
                <th>失败</th>
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
                </tr>
              ))}
            </tbody>
          </table>
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
                    <a className="pill pill--link" href={`/?session=${s.session_id}`}>
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
      </section>
    </main>
  );
}
