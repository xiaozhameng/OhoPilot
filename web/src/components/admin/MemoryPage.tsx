import { useCallback, useEffect, useState } from "react";
import { fetchMemory, saveMemory } from "../../api";
import type { MemoryFile } from "../../types";

/**
 * 长期记忆页：直接编辑 ~/.ohopilot/MEMORY.md 的原文。
 *
 * 为什么给编辑而不是只读：长期记忆本来就是"人写给模型的备忘录"，
 * agent 只会往里追加，过期事实和重复条目只有人能清理。
 * 编辑的是原文（含 > 注释行），保存的是原文——所见即所得；
 * 注入口径（去注释、截 4000）单独算出来摆在旁边，避免"我写的模型怎么没看到"。
 */
export function MemoryPage() {
  const [meta, setMeta] = useState<MemoryFile | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    fetchMemory()
      .then((file) => {
        setMeta(file);
        setDraft(file.content);
        setDirty(false);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  const save = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      const result = await saveMemory(draft);
      setMeta((prev) =>
        prev
          ? { ...prev, content: draft, chars: result.chars, injected_chars: result.injected_chars }
          : prev,
      );
      setDirty(false);
      setSavedAt(new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }, [draft]);

  if (error && !meta) {
    return <p className="settings__msg settings__msg--err">加载失败：{error}</p>;
  }
  if (!meta) return <p className="settings__note">加载中…</p>;

  const changed = dirty;
  // 注入占比给个直观条：超出 limit 的部分模型根本看不到
  const injectedPct = Math.min(100, Math.round((meta.injected_chars / meta.limit) * 100));

  return (
    <>
      <header className="page-head">
        <h1>记忆数据</h1>
        <p>
          长期记忆：跨会话的事实与偏好，每轮对话都会注入系统提示。agent 自己只往里追加；
          过期、重复、写错的条目靠你在这里整理。
        </p>
      </header>

      <section className="panel mem">
        <div className="mem__bar">
          <div className="mem__stats">
            <span title="文件总字符数">{meta.chars} 字符</span>
            <span className="mem__sep">·</span>
            <span title="去掉 > 注释行、截断后实际发给模型的字符数">
              注入 {meta.injected_chars} / {meta.limit}
            </span>
            <span className="cachebar cachebar--mem" title={`注入量占上限 ${injectedPct}%`}>
              <span className="cachebar__hit" style={{ width: `${injectedPct}%` }} />
            </span>
            {meta.injected_chars > meta.limit && (
              <span className="mem__over">超出部分模型看不到</span>
            )}
          </div>
          <div className="mem__actions">
            {savedAt && !changed && <span className="mem__saved">已保存 {savedAt}</span>}
            {changed && <span className="mem__dirty">有未保存修改</span>}
            <button
              className="btn btn--primary"
              disabled={saving || !changed}
              onClick={() => void save()}
            >
              {saving ? "保存中…" : "保存"}
            </button>
            {changed && (
              <button
                className="btn btn--ghost"
                onClick={() => {
                  setDraft(meta.content);
                  setDirty(false);
                  setError(null);
                }}
              >
                还原
              </button>
            )}
          </div>
        </div>

        <textarea
          className="mem__editor"
          spellCheck={false}
          value={draft}
          onChange={(ev) => {
            setDraft(ev.target.value);
            setDirty(true);
          }}
          onKeyDown={(ev) => {
            // ⌘S / Ctrl+S 保存：编辑器的肌肉记忆，不拦浏览器"存储网页"那次没意义
            if ((ev.metaKey || ev.ctrlKey) && ev.key === "s") {
              ev.preventDefault();
              if (!saving) void save();
            }
          }}
        />

        {error && <p className="settings__msg settings__msg--err">{error}</p>}

        <footer className="mem__foot">
          <code>{meta.path}</code>
          <span>
            以 <code>&gt;</code> 开头的行是注释，不会发给模型；条目建议写成 <code>- xxx</code>{" "}
            的列表项，人和模型都扫得动。
          </span>
        </footer>
      </section>
    </>
  );
}
