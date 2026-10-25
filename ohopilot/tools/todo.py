"""任务清单工具：模型维护「当前要做什么」的外置清单。

分工（宁窄勿宽）：
- todo_write：整表重写——列计划、整体改计划。写入的高危动作是
  "覆盖旧清单"，所以描述里明确提醒模型"重写会丢掉未完成条目"；
- todo_update：只动一条——勾状态、改文案。长任务执行期的高频操作，
  单独成工具让模型不用每次都重发整表（省 token 也少出错）。

返回值统一是全量清单 JSON：模型看到 id 才能做下一次 update，
也符合工具设计第 4 条（返回里带结构化线索）。
"""
import json
from typing import Any

from ohopilot.memory.todo import TodoStore
from ohopilot.tools.base import Tool, ToolResult


class TodoWriteTool(Tool):
    name = "todo_write"
    description = (
        "整表重写任务清单（旧清单会被完全覆盖）。"
        "什么时候用：接到 3 步以上的复杂任务时，先把计划列出来再动手；"
        "计划整体变化（用户改需求、某步做不下去要换路线）时重写。"
        "什么时候不用：只是给某一条打勾/改文字——用 todo_update；"
        "单步小任务不需要清单。"
        "写入前先想清楚：当前清单里未完成的条目会被覆盖掉。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "description": "完整任务列表，按执行顺序排列，每条一句话说清做什么",
            }
        },
        "required": ["items"],
    }

    def __init__(self, store: TodoStore):
        self._store = store

    async def run(self, **kwargs: Any) -> ToolResult:
        items = kwargs.get("items")
        if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
            return ToolResult.fail("items 必须是字符串数组，例如 [\"读取数据\", \"生成报告\"]")
        try:
            snapshot = self._store.replace_all(items)
        except ValueError as exc:
            return ToolResult.fail(f"{exc}；修正后重新调用 todo_write")
        return ToolResult.ok(
            "清单已建立（共 %d 条，全部待办）：\n%s" % (len(snapshot), json.dumps(snapshot, ensure_ascii=False))
        )


class TodoUpdateTool(Tool):
    name = "todo_update"
    description = (
        "更新任务清单中的一条：改状态或改文字。"
        "什么时候用：开始做某条时标 in_progress，做完标 done。"
        "每完成一条就立刻勾掉，不要攒到最后一起勾——用户面板靠它看进度。"
        "id 从 todo_write / todo_update 的返回里查。"
        "什么时候不用：要增删条目或调整顺序——用 todo_write 重写整表。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "id": {
                "type": "integer",
                "description": "要更新的条目 id",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "done"],
                "description": "新状态；不改状态就别传",
            },
            "content": {
                "type": "string",
                "description": "新内容；不改文字就别传",
            },
        },
        "required": ["id"],
    }

    def __init__(self, store: TodoStore):
        self._store = store

    async def run(self, **kwargs: Any) -> ToolResult:
        item_id = kwargs.get("id")
        if not isinstance(item_id, int):
            return ToolResult.fail("id 必须是整数，从上次 todo_write / todo_update 返回的清单里查")
        status = kwargs.get("status")
        content = kwargs.get("content")
        # 逆序校验放工具层而不是存储层：这是给模型的行为约束，
        # 带着补救建议返回，模型才知道怎么改。
        if status == "pending":
            snapshot = {item["id"]: item["status"] for item in self._store.list_items()}
            if snapshot.get(item_id) == "done":
                return ToolResult.fail(
                    f"条目 {item_id} 已经是 done，不能退回 pending；"
                    "如果是要重做这件事，用 todo_write 重写清单"
                )
        try:
            snapshot = self._store.update(item_id, status=status, content=content)
        except KeyError:
            return ToolResult.fail(
                f"清单里没有 id={item_id} 的条目；先看最近一次返回的清单拿到正确的 id"
            )
        except ValueError as exc:
            return ToolResult.fail(f"{exc}；修正参数后重试")
        return ToolResult.ok("已更新，当前清单：\n" + json.dumps(snapshot, ensure_ascii=False))


def build_todo_tools(store: TodoStore) -> list[Tool]:
    return [TodoWriteTool(store), TodoUpdateTool(store)]
