import { useEffect, useRef, useState } from "react";
import { IconPaperclip, IconSend, IconStop } from "./Icons";

interface ComposerProps {
  value: string;
  streaming: boolean;
  disabled: boolean;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  /** 选中文件后由 App 上传；上传中按钮转圈、输入框锁住 */
  onUpload?: (file: File) => Promise<void>;
}

export function Composer({
  value,
  streaming,
  disabled,
  onChange,
  onSend,
  onStop,
  onUpload,
}: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

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

  async function handleFilePicked(files: FileList | null) {
    const file = files?.[0];
    if (!file || !onUpload) return;
    setUploading(true);
    try {
      await onUpload(file);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
      ref.current?.focus();
    }
  }

  return (
    <div className="composer">
      <div className="composer__box">
        {onUpload && (
          <button
            className="composer__attach"
            onClick={() => fileRef.current?.click()}
            disabled={disabled || streaming || uploading}
            title={uploading ? "上传中…" : "上传文件（PDF/Word/Excel 等）"}
          >
            <IconPaperclip size={15} />
          </button>
        )}
        {/* file input 藏起来，回车/点击都走这个；清空 value 才能连续选同一个文件 */}
        <input
          ref={fileRef}
          type="file"
          hidden
          onChange={(e) => void handleFilePicked(e.target.files)}
        />
        <textarea
          ref={ref}
          className="composer__input"
          rows={1}
          placeholder={uploading ? "正在上传文件…" : "问点什么，或者让它动手改文件…"}
          value={value}
          disabled={disabled || uploading}
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
            disabled={disabled || uploading || !value.trim()}
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
