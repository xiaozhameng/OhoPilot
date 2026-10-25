import { useEffect, useState } from "react";
import { fetchUsage } from "../api";
import { estimateCost, fmtTokens } from "../cost";
import type { Block, Health, UsageCall, UsageSummary } from "../types";
import { IconClose } from "./Icons";

type ToolBlock = Extract<Block, { kind: "tool" }>;
export type DrawerMode = "tool" | "settings" | "usage" | null;

interface DrawerProps {
  mode: DrawerMode;
  tool: ToolBlock | null;
  health: Health | null;
  sessionId: string | null;
  onClose: () => void;
  onSaveSettings: (input: { model: string; workspace: string }) => Promise<void>;
}

export function Drawer({
  mode,
  tool,
  health,
  sessionId,
  onClose,
  onSaveSettings,
}: DrawerProps) {
  if (!mode) return null;

  const title =
    mode === "tool" ? "工具调用详情" : mode === "settings" ? "设置" : "Token 用量（开发者）";

  return (
    <div className="drawer">
      <div className="drawer__head">
        <strong>{title}</strong>
        <button className="icon-btn" onClick={onClose} title="关闭">
          <IconClose size={15} />
        </button>
      </div>
      <div className="drawer__body">
        {mode === "tool" && tool && <ToolDetail tool={tool} />}
        {mode === "settings" && (
          <SettingsForm health={health} onSaveSettings={onSaveSettings} />
        )}
        {mode === "usage" && <UsagePanel sessionId={sessionId} />}
      </div>
    </div>
  );
}

function ToolDetail({ tool }: { tool: ToolBlock }) {
  return (
    <div className="detail">
      <div className="detail__row">
        <span className="detail__key">工具</span>
        <code>{tool.name}</code>
      </div>
      <div className="detail__row">
        <span className="detail__key">状态</span>
        <span
          className={`badge ${
            tool.state === "running" ? "badge--run" : tool.isError ? "badge--err" : "badge--ok"
          }`}
        >
          {tool.state === "running" ? "执行中" : tool.isError ? "失败" : "完成"}
        </span>
      </div>
      <div className="detail__row">
        <span className="detail__key">调用 id</span>
        <code className="detail__id">{tool.callId}</code>
      </div>

      <h3 className="detail__title">参数</h3>
      <pre className="detail__pre">{JSON.stringify(tool.args, null, 2)}</pre>

      <h3 className="detail__title">
        返回
        {tool.result !== undefined && (
          <span className="detail__count">{tool.result.length} 字符</span>
        )}
      </h3>
      <pre className={`detail__pre${tool.isError ? " detail__pre--error" : ""}`}>
        {tool.state === "running" ? "等待结果…" : tool.result || "（无输出）"}
      </pre>
    </div>
  );
}

/** 价格表抽到 cost.ts 共用（控制台也要算）——这里只保留本组件用到的调用包装。 */

function callCost(call: UsageCall): number | null {
  return estimateCost(call.model, call.cache_hit_tokens, call.cache_miss_tokens, call.completion_tokens);
}

/**
 * 用量面板：总账卡片 + 每次调用的流水表。
 * sessionId 为 null 时看全部会话（全局视角）。
 */
function UsagePanel({ sessionId }: { sessionId: string | null }) {
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [calls, setCalls] = useState<UsageCall[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<"session" | "all">("session");

  useEffect(() => {
    const target = scope === "session" ? sessionId : null;
    fetchUsage(target)
      .then((body) => {
        setSummary(body.summary);
        setCalls(body.calls);
        setError(null);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, [sessionId, scope]);

  if (error) return <p className="settings__msg settings__msg--err">{error}</p>;
  if (!summary) return <p className="settings__note">加载中…</p>;

  const hitPct = Math.round(summary.cache_hit_rate * 1000) / 10;

  return (
    <div className="usage">
      <div className="usage__scope">
        <button
          className={`chip-btn${scope === "session" ? " chip-btn--on" : ""}`}
          disabled={!sessionId}
          onClick={() => setScope("session")}
        >
          当前会话
        </button>
        <button
          className={`chip-btn${scope === "all" ? " chip-btn--on" : ""}`}
          onClick={() => setScope("all")}
        >
          全部会话
        </button>
      </div>

      <div className="usage__cards">
        <div className="usage__card">
          <span className="usage__num">{summary.calls}</span>
          <span className="usage__label">模型调用次数</span>
        </div>
        <div className="usage__card">
          <span className="usage__num">{fmtTokens(summary.total_tokens)}</span>
          <span className="usage__label">总 tokens</span>
        </div>
        <div className="usage__card">
          <span className={`usage__num${hitPct < 50 ? " usage__num--warn" : ""}`}>
            {summary.calls ? `${hitPct}%` : "—"}
          </span>
          <span className="usage__label">缓存命中率</span>
        </div>
        <div className="usage__card">
          <span className="usage__num">
            {summary.avg_first_token_ms
              ? `${(summary.avg_first_token_ms / 1000).toFixed(1)}s`
              : "—"}
          </span>
          <span className="usage__label">平均首字延迟</span>
        </div>
      </div>

      {hitPct < 50 && summary.calls > 0 && (
        <p className="settings__msg">
          命中率低于 50%：检查系统提示里是否有每次都变的内容（时间戳、随机数），
          它们会把缓存前缀打断，让每次请求都按全价计费。
        </p>
      )}

      {calls.length === 0 ? (
        <p className="settings__note">还没有调用记录。发一轮对话后再来看。</p>
      ) : (
        <table className="usage__table">
          <thead>
            <tr>
              <th>轮</th>
              <th>prompt</th>
              <th>命中</th>
              <th>未命中</th>
              <th>输出</th>
              <th>耗时</th>
              <th>约 ¥</th>
            </tr>
          </thead>
          <tbody>
            {calls.map((call) => {
              const cost = callCost(call);
              return (
                <tr key={call.id}>
                  <td>{call.round_no}</td>
                  <td>{fmtTokens(call.prompt_tokens)}</td>
                  <td className="usage__hit">{fmtTokens(call.cache_hit_tokens)}</td>
                  <td>{fmtTokens(call.cache_miss_tokens)}</td>
                  <td>{fmtTokens(call.completion_tokens)}</td>
                  <td>{(call.duration_ms / 1000).toFixed(1)}s</td>
                  <td>{cost === null ? "—" : cost.toFixed(4)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <p className="settings__note">
        「命中」是缓存前缀复用的部分（约 1/10 计价），「未命中」按全价。
        价格表写在前端 PRICE_PER_M，以模型官网为准。
        每轮上下文构成（system/history/tool_results）按字符数记录，token 是近似值。
      </p>
    </div>
  );
}

/**
 * 设置：改的是服务端进程里的 cfg 对象，影响**之后**所有对话。
 * 所以保存时不做乐观更新，等后端回 200 再刷新界面上的值。
 */
function SettingsForm({
  health,
  onSaveSettings,
}: {
  health: Health | null;
  onSaveSettings: (input: { model: string; workspace: string }) => Promise<void>;
}) {
  const [model, setModel] = useState(health?.model ?? "");
  const [workspace, setWorkspace] = useState(health?.workspace ?? "");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  // 抽屉每次打开都重新同步一次：服务端的值可能在别处被改过
  useEffect(() => {
    setModel(health?.model ?? "");
    setWorkspace(health?.workspace ?? "");
    setMessage(null);
    setFailed(false);
  }, [health?.model, health?.workspace]);

  async function save() {
    setSaving(true);
    setMessage(null);
    try {
      await onSaveSettings({ model: model.trim(), workspace: workspace.trim() });
      setFailed(false);
      setMessage("已保存，之后的对话生效");
    } catch (err) {
      setFailed(true);
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="settings">
      <label className="field">
        <span className="field__label">模型</span>
        <input
          className="field__input"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="deepseek-v4-flash"
        />
        <span className="field__hint">对应 OHOPILOT_MODEL，换模型不用重启</span>
      </label>

      <label className="field">
        <span className="field__label">工作目录</span>
        <input
          className="field__input"
          value={workspace}
          onChange={(e) => setWorkspace(e.target.value)}
          placeholder="/Users/you/project"
        />
        <span className="field__hint">文件工具与 shell 的硬边界，必须是已存在的目录</span>
      </label>

      <button className="btn btn--primary btn--block" onClick={save} disabled={saving}>
        {saving ? "保存中…" : "保存"}
      </button>

      {message && (
        <p className={`settings__msg${failed ? " settings__msg--err" : ""}`}>{message}</p>
      )}

      <h3 className="detail__title">当前可用工具（{health?.tools.length ?? 0}）</h3>
      <div className="chips">
        {health?.tools.map((name) => (
          <code key={name} className="chip">
            {name}
          </code>
        ))}
      </div>

      <p className="settings__note">
        这个服务只能绑定本机地址。它能执行 shell 命令，不要把端口暴露到外网。
      </p>
    </div>
  );
}
