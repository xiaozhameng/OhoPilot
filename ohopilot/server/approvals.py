"""审批桥：把「终端 y/n」换成「挂起等 HTTP 回调」。

终端里的审批是同步阻塞的（input() 等人敲键），服务端不能这么干——
一个请求挂住不算，还会把事件循环堵死，所有会话一起卡。

做法（这是整个服务端最巧的一处）：
  1. agent 要审批 → 调 approver → 我们在这里创建一个 Future，挂进 _pending；
  2. approver 在这个 Future 上 await，于是「这一轮问答」就停在半路等；
  3. 事件流已经把 approval_request 推给前端了，前端用户点「允许」→ POST /approvals/{call_id}；
  4. 那个端点调 resolve() 把 Future 填上结果 → 第 2 步的 await 立刻返回。
     await 挂起的是「这个协程」，不是「整个事件循环」——别的请求照常跑。

再加一道超时：前端一直不点，到点按拒绝处理（和终端「问不了 = 不」同一条原则）。
"""
from __future__ import annotations

import asyncio


class ApprovalBroker:
    def __init__(self, timeout: float = 180.0) -> None:
        self.timeout = timeout
        #: call_id → 等待中的 Future。多个会话可以各挂一个，互不干扰
        self._pending: dict[str, asyncio.Future[bool]] = {}

    async def approver(self, call_id: str, name: str, arguments: dict, reason: str) -> bool:
        """实现 agent.Approver 协议：挂起直到有人回答，或超时。"""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._pending[call_id] = future
        try:
            return await asyncio.wait_for(future, timeout=self.timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            # 超时 = 没人管 = 拒绝；CancelledError 是客户端断开了 SSE，同样按拒绝
            return False
        finally:
            self._pending.pop(call_id, None)

    def resolve(self, call_id: str, allow: bool) -> bool:
        """前端回话。返回是否真的有一个调用在等这个 id。"""
        future = self._pending.get(call_id)
        if future is None or future.done():
            return False
        future.set_result(allow)
        return True

    @property
    def pending_count(self) -> int:
        return len(self._pending)
