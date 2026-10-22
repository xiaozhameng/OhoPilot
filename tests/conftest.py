"""测试公共夹具。

三条原则（决定了这些 fixture 为什么长这样）：

1. **绝不碰真实的 `~/.ohopilot`**。每个测试都用 tmp_path 里的 home，
   否则跑一次测试就会把用户真实的会话库、长期记忆弄脏。
2. **绝不发真实网络请求**。模型调用一律在测试里替换掉，
   测试要么全绿要么全红，不能因为今天网络抖动变红。
3. **配置直接构造，不走 Config.load()**。load() 读环境变量与 .env，
   同一台机器上跑出来的结果会随 shell 环境变化——测试要可复现。
"""

from __future__ import annotations

import pytest

from ohopilot.config import Config
from ohopilot.memory.store import SessionStore


@pytest.fixture
def workspace(tmp_path):
    """一个空的工作目录，充当 agent 的活动边界。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


@pytest.fixture
def cfg(tmp_path, workspace) -> Config:
    """完全隔离的配置：home 和 workspace 都落在临时目录里。"""
    home = tmp_path / "home"
    home.mkdir()
    return Config(home=home, workspace=workspace, api_key="sk-test-not-real")


@pytest.fixture
def store(cfg):
    """一个临时 SQLite 会话库，用完关掉。"""
    session_store = SessionStore(cfg.db_path)
    yield session_store
    session_store.close()


@pytest.fixture
def writer(workspace):
    """往工作目录里写测试素材的小工具，省得每个测试重复 mkdir + write_text。"""

    def _write(relative: str, content: str = "x") -> str:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return relative

    return _write
