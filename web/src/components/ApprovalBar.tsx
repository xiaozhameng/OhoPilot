import type { PendingApproval } from "../types";
import { IconWarning } from "./Icons";

interface ApprovalBarProps {
  pending: PendingApproval[];
  onAnswer: (callId: string, allow: boolean) => void;
}

/**
 * 审批卡片：浮在输入框上方，而不是弹模态框。
 *
 * 为什么不用模态：agent 一轮里可能连续要两三次审批，模态会反复打断视线；
 * 而且这是「当前这一轮被挂住了」的状态，贴在对话里比盖住对话更符合事实。
 */
export function ApprovalBar({ pending, onAnswer }: ApprovalBarProps) {
  if (pending.length === 0) return null;
  const current = pending[0];

  return (
    <div className="approval">
      <div className="approval__head">
        <IconWarning size={16} />
        <strong>需要你确认</strong>
        <span className="approval__tool">{current.name}</span>
        {pending.length > 1 && <span className="approval__more">还有 {pending.length - 1} 个</span>}
      </div>

      <p className="approval__reason">{current.reason}</p>
      <pre className="approval__args">{JSON.stringify(current.args, null, 2)}</pre>

      <div className="approval__actions">
        <button className="btn btn--ghost" onClick={() => onAnswer(current.callId, false)}>
          拒绝
        </button>
        <button className="btn btn--primary" onClick={() => onAnswer(current.callId, true)}>
          允许执行
        </button>
      </div>
    </div>
  );
}
