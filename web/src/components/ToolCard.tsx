import { useState } from "react";
import type { Block } from "../types";
import { IconChevron, IconWrench } from "./Icons";

type ToolBlock = Extract<Block, { kind: "tool" }>;

interface ToolCardProps {
  block: ToolBlock;
  /** 点「详情」时把这一个块交给抽屉，抽屉里看完整参数与完整返回 */
  onOpen: (block: ToolBlock) => void;
}

/** 参数压成一行：卡片上只给一眼能看懂的量，完整内容进抽屉。 */
function brief(args: Record<string, unknown>, limit = 72): string {
  const text = JSON.stringify(args);
  if (!text || text === "{}") return "";
  return text.length <= limit ? text : `${text.slice(0, limit)}…`;
}

export function ToolCard({ block, onOpen }: ToolCardProps) {
  const [open, setOpen] = useState(false);
  const running = block.state === "running";
  const status = running ? "running" : block.isError ? "error" : "ok";
  const statusText = running ? "执行中" : block.isError ? "失败" : "完成";

  return (
    <div className={`tool tool--${status}${open ? " tool--open" : ""}`}>
      <button
        className="tool__head"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title={open ? "收起" : "展开"}
      >
        <span className={`tool__status tool__status--${status}`} />
        <IconWrench size={13} />
        <span className="tool__name">{block.name}</span>
        {!open && <span className="tool__args">{brief(block.args)}</span>}
        <span className="tool__state">{statusText}</span>
        <IconChevron size={13} className={`tool__chevron${open ? " tool__chevron--open" : ""}`} />
      </button>

      {open && (
        <div className="tool__body">
          <div className="tool__label">参数</div>
          <pre className="tool__pre">{JSON.stringify(block.args, null, 2)}</pre>

          <div className="tool__label">
            返回
            {block.result !== undefined && (
              <span className="tool__count">{block.result.length} 字符</span>
            )}
          </div>
          <pre className={`tool__pre${block.isError ? " tool__pre--error" : ""}`}>
            {running ? "等待结果…" : block.result || "（无输出）"}
          </pre>

          <button className="tool__more" onClick={() => onOpen(block)}>
            在详情里查看
          </button>
        </div>
      )}
    </div>
  );
}
