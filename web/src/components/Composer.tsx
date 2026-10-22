import { useEffect, useRef } from "react";
import { IconSend, IconStop } from "./Icons";

interface ComposerProps {
  value: string;
  streaming: boolean;
  disabled: boolean;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
}

export function Composer({ value, streaming, disabled, onChange, onSend, onStop }: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // 高度跟着内容长，但封顶 200px —— 再长就自己滚，否则输入框会把对话挤没
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  // 提交后焦点还在输入框里，不用每次点一下才能继续打字
  useEffect(() => {
    if (!streaming) ref.current?.focus();
  }, [streaming]);

  return (
    <div className="composer">
      <div className="composer__box">
        <textarea
          ref={ref}
          className="composer__input"
          rows={1}
          placeholder="问点什么，或者让它动手改文件…"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            // Enter 发送、Shift+Enter 换行；中文输入法选词时的 Enter 不能当发送
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              onSend();
            }
          }}
        />
        {streaming ? (
          <button className="composer__send composer__send--stop" onClick={onStop} title="停止">
            <IconStop size={15} />
          </button>
        ) : (
          <button
            className="composer__send"
            onClick={onSend}
            disabled={disabled || !value.trim()}
            title="发送（Enter）"
          >
            <IconSend size={15} />
          </button>
        )}
      </div>
      <div className="composer__hint">
        Enter 发送 · Shift + Enter 换行 · 危险操作会先弹出确认
      </div>
    </div>
  );
}
