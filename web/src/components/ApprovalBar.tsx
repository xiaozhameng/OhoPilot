import type { ApprovalOption, PendingApproval } from "../types";
import { IconWarning } from "./Icons";

interface ApprovalBarProps {
  pending: PendingApproval[];
  onAnswer: (callId: string, allow: boolean, kind?: "once" | "file" | "dir", path?: string) => void;
}

/** 路径太长会把按钮撑爆，中间省略比截掉尾部更有用（尾部往往是文件名） */
function middleEllipsis(text: string, keep = 42): string {
  if (text.length <= keep) return text;
  const head = Math.ceil((keep - 1) / 2);
  const tail = Math.floor((keep - 1) / 2);
  return `${text.slice(0, head)}…${text.slice(-tail)}`;
}

/**
 * 审批卡片：浮在输入框上方，而不是弹模态框。
 *
 * 为什么不用模态：agent 一轮里可能连续要两三次审批，模态会反复打断视线；
 * 而且这是「当前这一轮被挂住了」的状态，贴在对话里比盖住对话更符合事实。
 *
 * 为什么把「允许这个目录」做成单独的按钮行（第 32 步）：
 * 让 agent 在桌面建个项目目录，一个文件点一次允许，十分钟里能点二十次，
 * 点到最后人是麻的——"反正都允许"比审批本身更危险。给出范围选项，
 * 一次批准就把这一整类操作收掉，审批才回到"确实在把关"的状态。
 */
export function ApprovalBar({ pending, onAnswer }: ApprovalBarProps) {
  if (pending.length === 0) return null;
  const current = pending[0];

  // 只留 dir：file 的语义和"仅本次"几乎一样（下个文件还是要问），
  // 列出来只会让人以为选错了
  const dirOptions: ApprovalOption[] = current.options.filter((o) => o.kind === "dir");

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
          只允许这一次
        </button>
      </div>

      {dirOptions.length > 0 && (
        <div className="approval__scopes">
          <span className="approval__scopes-label" title="批准后这些目录内的同类操作不再询问，可在控制台撤销">
            以后都不用问：
          </span>
          {dirOptions.map((option) => (
            <button
              key={option.path}
              className="approval__scope"
              title={option.path}
              onClick={() => onAnswer(current.callId, true, "dir", option.path)}
            >
              {option.label.split("：")[0]} · {middleEllipsis(option.path)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
