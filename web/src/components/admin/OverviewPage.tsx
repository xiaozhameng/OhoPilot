import { useEffect, useState } from "react";
import {
  Area,
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchConsoleOverview } from "../../api";
import { estimateCost, fmtTokens } from "../../cost";
import type { ConsoleOverview } from "../../types";

/**
 * 总览页：全局健康度一屏看完。
 *
 * 图表用 recharts（第 33 步从手搓 div 柱状图升级）：
 * 手搓版只有一根柱、没有坐标轴没有 hover 数值，"趋势"其实看不出趋势；
 * recharts 给我们：双轴（tokens 用面积、调用次数用柱）、悬停 tooltip、
 * 环形图看缓存命中占比——同一个数据，读出来的信息多一个量级。
 * 颜色全部用 var(--xxx)：SVG 属性接受 CSS 变量，深色模式自动跟随。
 */
export function OverviewPage() {
  const [data, setData] = useState<ConsoleOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchConsoleOverview()
      .then(setData)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  if (error) return <p className="settings__msg settings__msg--err">加载失败：{error}</p>;
  if (!data) return <p className="settings__note">加载中…</p>;

  const usage = data.usage;
  const hitPct = Math.round(usage.cache_hit_rate * 1000) / 10;
  const toolErrorPct =
    data.tool_calls_total > 0
      ? Math.round((data.tool_errors_total / data.tool_calls_total) * 1000) / 10
      : 0;

  // 缓存分布：命中 / 未命中两块。老记录没存拆分（全 0），退化为"暂无数据"
  const hitTokens = data.by_model.reduce((sum, m) => sum + m.cache_hit_tokens, 0);
  const missTokens = data.by_model.reduce((sum, m) => sum + m.cache_miss_tokens, 0);
  const cacheData =
    hitTokens + missTokens > 0
      ? [
          { name: "命中缓存", value: hitTokens, color: "var(--ok)" },
          { name: "未命中", value: missTokens, color: "var(--accent)" },
        ]
      : [];

  return (
    <>
      <header className="admin__page-head">
        <h1>数据概览</h1>
        <p>跨会话聚合 · 缓存命中率低于 50% 通常意味着前缀不稳定，优先修它</p>
      </header>

      <section className="statgrid">
        <div className="statgrid__card">
          <span className="statgrid__num">{usage.calls}</span>
          <span className="statgrid__label">模型调用</span>
        </div>
        <div className="statgrid__card">
          <span className="statgrid__num">{fmtTokens(usage.total_tokens)}</span>
          <span className="statgrid__label">总 tokens</span>
        </div>
        <div className="statgrid__card">
          <span className={`statgrid__num${usage.calls && hitPct < 50 ? " statgrid__num--warn" : ""}`}>
            {usage.calls ? `${hitPct}%` : "—"}
          </span>
          <span className="statgrid__label">缓存命中率</span>
        </div>
        <div className="statgrid__card">
          <span className="statgrid__num">
            {usage.avg_first_token_ms ? `${(usage.avg_first_token_ms / 1000).toFixed(1)}s` : "—"}
          </span>
          <span className="statgrid__label">平均首字延迟</span>
        </div>
        <div className="statgrid__card">
          <span className="statgrid__num">{data.tool_calls_total}</span>
          <span className="statgrid__label">工具调用</span>
        </div>
        <div className="statgrid__card">
          <span className={`statgrid__num${toolErrorPct > 10 ? " statgrid__num--warn" : ""}`}>
            {data.tool_calls_total ? `${toolErrorPct}%` : "—"}
          </span>
          <span className="statgrid__label">工具错误率</span>
        </div>
      </section>

      <section className="panel">
        <h2 className="panel__title">近 14 天趋势</h2>
        {data.trend.length === 0 ? (
          <p className="settings__note">还没有调用记录。</p>
        ) : (
          <div className="rchart">
            <ResponsiveContainer width="100%" height={260}>
              <ComposedChart data={data.trend} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                <XAxis
                  dataKey="date"
                  tickFormatter={(v: string) => v.slice(5)}
                  tick={{ fill: "var(--muted)", fontSize: 11 }}
                  axisLine={{ stroke: "var(--border)" }}
                  tickLine={false}
                />
                <YAxis
                  yAxisId="tokens"
                  tickFormatter={(v: number) => fmtTokens(v)}
                  tick={{ fill: "var(--muted)", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  width={52}
                />
                <YAxis
                  yAxisId="calls"
                  orientation="right"
                  allowDecimals={false}
                  tick={{ fill: "var(--muted)", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  width={30}
                />
                <Tooltip content={<TrendTooltip />} cursor={{ fill: "rgba(127,137,158,0.08)" }} />
                <Area
                  yAxisId="tokens"
                  type="monotone"
                  dataKey="tokens"
                  name="tokens"
                  stroke="var(--accent)"
                  strokeWidth={2}
                  fill="var(--accent-soft)"
                  fillOpacity={0.9}
                  isAnimationActive={false}
                />
                <Bar
                  yAxisId="calls"
                  dataKey="llm_calls"
                  name="模型调用"
                  fill="var(--border-strong)"
                  barSize={10}
                  radius={[3, 3, 0, 0]}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
            <p className="rchart__legend">
              <span className="rchart__key rchart__key--area">tokens（左轴）</span>
              <span className="rchart__key rchart__key--bar">模型调用次数（右轴）</span>
            </p>
          </div>
        )}
      </section>

      <div className="panels-row">
        <section className="panel">
          <h2 className="panel__title">缓存命中分布</h2>
          {cacheData.length === 0 ? (
            <p className="settings__note">
              还没有带缓存拆分的记录（第 31 步之前的调用没存这两个字段）。
            </p>
          ) : (
            <div className="rchart rchart--donut">
              <ResponsiveContainer width="100%" height={180}>
                <PieChart>
                  <Pie
                    data={cacheData}
                    dataKey="value"
                    nameKey="name"
                    innerRadius={52}
                    outerRadius={80}
                    paddingAngle={2}
                    strokeWidth={0}
                    isAnimationActive={false}
                  >
                    {cacheData.map((entry) => (
                      <Cell key={entry.name} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip content={<DonutTooltip />} />
                </PieChart>
              </ResponsiveContainer>
              <div className="rchart__center">
                <strong>{hitPct}%</strong>
                <span>命中率</span>
              </div>
              <p className="rchart__legend">
                <span className="rchart__key rchart__key--ok">命中 {fmtTokens(hitTokens)}</span>
                <span className="rchart__key rchart__key--accent">
                  未命中 {fmtTokens(missTokens)}
                </span>
              </p>
            </div>
          )}
        </section>

        <section className="panel panel--grow">
          <h2 className="panel__title">按模型拆分</h2>
          {data.by_model.length === 0 ? (
            <p className="settings__note">还没有调用记录。</p>
          ) : (
            <table className="dtable">
              <thead>
                <tr>
                  <th>模型</th>
                  <th>调用</th>
                  <th>输入(命中)</th>
                  <th>输入(未命中)</th>
                  <th>思考</th>
                  <th>输出</th>
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
                    <td className="dtable__good">{fmtTokens(m.cache_hit_tokens)}</td>
                    <td>{fmtTokens(m.cache_miss_tokens)}</td>
                    <td>{m.reasoning_tokens ? fmtTokens(m.reasoning_tokens) : "—"}</td>
                    <td>{fmtTokens(m.completion_tokens)}</td>
                    <td>{fmtTokens(m.total_tokens)}</td>
                    <td>
                      {estimateCost(m.model, m.cache_hit_tokens, m.cache_miss_tokens, m.completion_tokens)?.toFixed(
                        4,
                      ) ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="settings__note">
            输入按「命中 / 未命中」两列拆开（两者单价常差 10 倍）；思考列只有推理模型有值。
            老记录没存缓存拆分，¥ 列按 0 命中估算会偏低，以新数据为准。
          </p>
        </section>
      </div>
    </>
  );
}

/** 悬停提示。自定义组件才能吃到主题变量——recharts 默认的白底黑字在深色模式下突兀。 */
function TrendTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color?: string }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rchart__tip">
      <div className="rchart__tip-title">{label}</div>
      {payload.map((p) => (
        <div key={p.name} className="rchart__tip-row">
          <span className="rchart__tip-dot" style={{ background: p.color }} />
          {p.name === "tokens" ? `${fmtTokens(p.value)} tokens` : `${p.value} 次模型调用`}
        </div>
      ))}
    </div>
  );
}

function DonutTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ name: string; value: number }>;
}) {
  if (!active || !payload?.length) return null;
  const item = payload[0];
  return (
    <div className="rchart__tip">
      <div className="rchart__tip-row">
        {item.name}：{fmtTokens(item.value)} tokens
      </div>
    </div>
  );
}
