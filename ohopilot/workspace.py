"""这一轮该用哪个工作目录（第 39 步：空间绑目录真正生效）。

## 为什么需要这一层

`cfg.workspace` 是**进程级**的一个值。以前只有终端入口，一个进程跑一个会话，
它够用。现在服务端一个进程里可能同时跑多个会话（每个会话一把锁，不同会话
并发执行），如果 A 会话把 `cfg.workspace` 改成 `/proj/a`、B 会话正在跑
`/proj/b` 的读写，两边的边界校验就会互相串——这是最坏的一类 bug：
不报错，只是偶尔把文件写到别的项目里。

所以工作目录存进 **ContextVar**：
  - asyncio 每创建一个 Task 都会拷贝一份当前上下文，任务之间天然隔离；
  - `asyncio.to_thread` 也会把上下文带进线程池，工具里同步执行的路径解析同样拿得到；
  - 工具在**调用时**才读（不是构造时缓存），所以同一个工具实例能服务不同会话。

## 语义

- `active_workspace(cfg)`：当前生效的目录。没绑定就是 `cfg.workspace`（进程默认值，
  也就是 `/settings` 里能改的那个）。
- `use_workspace(path)`：在这个代码块里把目录切成 path。传 None = 不切。

## 举例

默认工作目录 `/Users/me/Desktop/AI软件开发/Agent/OhoPilot`，
空间「支付项目」绑了 `/Users/me/work/pay`：

| 场景 | 会话 | active_workspace() | 模型说「读 README.md」实际读的是 |
|---|---|---|---|
| 空间绑了目录 | 在「支付项目」里 | `/Users/me/work/pay` | `/Users/me/work/pay/README.md` |
| 默认空间（没绑） | 在「默认空间」里 | `/Users/…/OhoPilot` | `/Users/…/OhoPilot/README.md` |
| 目录被删了 | 在「支付项目」里 | `/Users/…/OhoPilot`（回退） | 回退后的路径，并给用户一条提示 |

回退而不是报错：盘符换了、目录被重命名这类事很常见，一次对话直接失败
不如降级到默认目录继续干，同时在界面上说清"这次没按你绑的目录走"。
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

from ohopilot.config import Config

#: 当前任务实际生效的工作目录。None = 没人为这次调用指定过，走 cfg.workspace。
_active: ContextVar[Path | None] = ContextVar("ohopilot_active_workspace", default=None)


def active_workspace(cfg: Config) -> Path:
    """当前这一轮该用的工作目录。

    所有工具与系统提示都通过它取目录，而不再直接读 `cfg.workspace`——
    只有这样「按会话切目录」才是全局一致的：模型被告知的目录、
    文件工具校验的边界、shell 的 cwd，三处用的是同一个值。
    """
    return _active.get() or cfg.workspace


@contextlib.contextmanager
def use_workspace(path: Path | None) -> Iterator[None]:
    """在 with 块内把工作目录切成 path；None 表示什么都不做。

    必须成对 reset：块内抛异常时也要把上下文还回去，否则同一任务里
    后续的调用会莫名其妙继续用上一个目录。
    """
    if path is None:
        yield
        return

    token = _active.set(path)
    try:
        yield
    finally:
        _active.reset(token)
