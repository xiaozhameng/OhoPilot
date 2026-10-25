/**
 * 常驻任务面板：渲染当前会话的任务清单。
 *
 * 数据来源两条路：
 * 1. 对话中：后端 todo_* 工具一有动作就推 todo 事件（全量快照）；
 * 2. 打开旧会话/刷新页面：App 里调 fetchSessionTodos 恢复。
 * 两条路最后都收敛到 state.todos，这里只管渲染，不关心数据怎么来的。
 *
 * 清单为空时返回 null——大多数问答用不到清单，面板不该占地方。
 */

import type { TodoItem } from "../types";

const ICON: Record<TodoItem["status"], string> = {
  pending: "☐",
  in_progress: "▸",
  done: "☑",
};

const STATUS_LABEL: Record<TodoItem["status"], string> = {
  pending: "待办",
  in_progress: "进行中",
  done: "完成",
};

export function TodoPanel({ items }: { items: TodoItem[] }) {
  if (items.length === 0) return null;

  const doneCount = items.filter((i) => i.status === "done").length;
  const allDone = doneCount === items.length;

  return (
    <div className={`todo-panel${allDone ? " todo-panel--done" : ""}`}>
      <div className="todo-panel__head">
        <span className="todo-panel__title">任务清单</span>
        <span className="todo-panel__progress">
          {doneCount}/{items.length}
        </span>
      </div>
      <ul className="todo-panel__list">
        {items.map((item) => (
          <li key={item.id} className={`todo-item todo-item--${item.status}`}>
            <span className="todo-item__icon" aria-hidden>
              {ICON[item.status]}
            </span>
            <span className="todo-item__content">{item.content}</span>
            <span className="todo-item__status">{STATUS_LABEL[item.status]}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
