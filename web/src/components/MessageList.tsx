import { useEffect, useRef } from "react";
import type { Block, ChatMessage } from "../types";
import { Logo } from "./Icons";
import { ToolCard } from "./ToolCard";

type ToolBlock = Extract<Block, { kind: "tool" }>;

interface MessageListProps {
  messages: ChatMessage[];
  onOpenTool: (block: ToolBlock) => void;
  onSuggest: (text: string) => void;
}

const SUGGESTIONS = [
  "把这个项目的目录结构讲一遍",
  "在 README 里补一节「怎么换模型」",
  "统计每个 py 文件的行数，按多少排序",
  "记住：回答我时先给结论，再给理由",
];

export function MessageList({ messages, onOpenTool, onSuggest }: MessageListProps) {
  const endRef = useRef<HTMLDivElement>(null);

  // 每次消息变化（尤其是流式追加）都滚到底部。
  // 用 scrollIntoView 而不是改 scrollTop：输入框高度变化时它也能跟着算对。
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="empty">
        <div className="empty__logo">
          <Logo size={44} />
        </div>
        <h1>今天想做点什么？</h1>
        <p>它能读写工作目录里的文件、执行命令、抓网页。危险动作会先问你。</p>
        <div className="empty__suggestions">
          {SUGGESTIONS.map((text) => (
            <button key={text} className="suggestion" onClick={() => onSuggest(text)}>
              {text}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="thread">
      {messages.map((message) => (
        <Message key={message.id} message={message} onOpenTool={onOpenTool} />
      ))}
      <div ref={endRef} />
    </div>
  );
}

function Message({
  message,
  onOpenTool,
}: {
  message: ChatMessage;
  onOpenTool: (block: ToolBlock) => void;
}) {
  const isUser = message.role === "user";
  const text = message.blocks
    .filter((b): b is Extract<Block, { kind: "text" }> => b.kind === "text")
    .map((b) => b.text)
    .join("\n");
  const busy = message.streaming && message.blocks.length === 0;

  return (
    <div className={`msg msg--${message.role}`}>
      {!isUser && (
        <div className="msg__avatar">
          <Logo size={22} />
        </div>
      )}

      <div className="msg__body">
        {isUser ? (
          <div className="bubble">{text}</div>
        ) : (
          <>
            {message.blocks.map((block, i) => (
              <BlockView key={i} block={block} onOpenTool={onOpenTool} />
            ))}
            {busy && (
              <div className="thinking">
                <span />
                <span />
                <span />
              </div>
            )}
            {message.streaming && message.blocks.length > 0 && <span className="caret" />}
          </>
        )}
      </div>
    </div>
  );
}

function BlockView({ block, onOpenTool }: { block: Block; onOpenTool: (b: ToolBlock) => void }) {
  switch (block.kind) {
    case "text":
      return <div className="answer">{block.text}</div>;
    case "tool":
      return <ToolCard block={block} onOpen={onOpenTool} />;
    case "notice":
      return <div className="notice">{block.message}</div>;
    case "error":
      return <div className="error-box">{block.message}</div>;
    default:
      return null;
  }
}
