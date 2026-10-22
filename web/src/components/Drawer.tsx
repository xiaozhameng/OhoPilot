import { useEffect, useState } from "react";
import type { Block, Health } from "../types";
import { IconClose } from "./Icons";

type ToolBlock = Extract<Block, { kind: "tool" }>;
export type DrawerMode = "tool" | "settings" | null;

interface DrawerProps {
  mode: DrawerMode;
  tool: ToolBlock | null;
  health: Health | null;
  onClose: () => void;
  onSaveSettings: (input: { model: string; workspace: string }) => Promise<void>;
}

export function Drawer({ mode, tool, health, onClose, onSaveSettings }: DrawerProps) {
  if (!mode) return null;

  return (
    <div className="drawer">
      <div className="drawer__head">
        <strong>{mode === "tool" ? "工具调用详情" : "设置"}</strong>
        <button className="icon-btn" onClick={onClose} title="关闭">
          <IconClose size={15} />
        </button>
      </div>
      <div className="drawer__body">
        {mode === "tool" && tool && <ToolDetail tool={tool} />}
        {mode === "settings" && (
          <SettingsForm health={health} onSaveSettings={onSaveSettings} />
        )}
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
