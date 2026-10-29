/**
 * 管理后台各页共用的格式化工具。
 * 放在这里而不是 cost.ts：cost.ts 管「钱」，这里管「展示」，职责别混。
 */

/** 毫秒 → 人话。超过 10 秒就不显示小数了（18.3s 和 18.7s 没区别） */
export function fmtMs(ms: number | null | undefined): string {
  if (ms == null || ms === 0) return "—";
  if (ms >= 10_000) return `${(ms / 1000).toFixed(0)}s`;
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}

/** finish_reason → 中文说明。length 是最值得警惕的：说明回答被截断了 */
export function finishLabel(reason: string): { text: string; warn: boolean } {
  switch (reason) {
    case "stop":
      return { text: "正常结束", warn: false };
    case "tool_calls":
      return { text: "转工具", warn: false };
    case "length":
      return { text: "被截断", warn: true };
    case "":
      return { text: "—", warn: false };
    default:
      return { text: reason, warn: false };
  }
}

/** 缓存分布条的两个百分比。入参是命中/未命中 token 数 */
export function cacheSplit(
  hit: number,
  miss: number,
): { hitPct: number; total: number } {
  const total = hit + miss;
  return { hitPct: total > 0 ? Math.round((hit / total) * 100) : 0, total };
}
