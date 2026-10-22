# OhoPilot 从零构建路线图

> 这是我们的进度清单，**不是代码**。每一步由你亲手写，我负责讲清「为什么」，并在你写完后 review。
> 不需要它随时删，不影响任何代码。

## 前置条件

- 一把有效的 DeepSeek API Key（第 6 步之后开始要用；现在填好也可以，填进 `.env` 的 `OHOPILOT_API_KEY=`）
- 环境已就绪：`~/Desktop/AI软件开发/Agent/OhoPilot/.venv`（Python 3.13，依赖已装好，不用重装）

## 每一步的固定节奏

1. 我讲：这一步做什么、为什么现在做、在整体里占哪一块
2. 我给：要实现的东西（接口与行为，不给答案）＋ 一条验收命令
3. 你写 → 跑验收命令
4. 你把结果发我 → 我 review 你的代码，指出问题、讲清原因
5. 通过，进入下一步

卡住时的求助顺序：先看报错信息 → 再问我 → 实在没头绪时，再看参考实现。

## 阶段一：最小可用内核（8 个文件，先跑通它）

- [x] 1. 项目骨架 + `pyproject.toml` —— 让工程可安装、生成 `oho` 命令
- [x] 2. `ohopilot/config.py` —— 统一读取模型名 / 密钥 / 工作目录
- [x] 3. `ohopilot/core/events.py` —— 定下内核与界面之间的「词汇表」
- [x] 4. `ohopilot/tools/base.py` —— 定义「工具」的模具
- [x] 5. `ohopilot/tools/fs.py` —— 第一个工具：读文件
- [x] 6. `ohopilot/core/llm.py` —— 把模型调用接进来（LiteLLM）
- [x] 7. `ohopilot/core/agent.py` —— 主循环（整个软件的心脏）
- [x] 8. `ohopilot/cli.py` —— 最小命令行入口
- [x] 9. 跑通第一次真实对话（用 `oho` 在终端里聊）

## 阶段二：加厚（内核跑通之后）

- [x] 10. 主循环加厚：多轮工具 / 轮次上限 / 错误回填 / 危险操作审批
- [x] 11. `ohopilot/memory/store.py` —— 会话记忆 + 长期记忆
- [x] 12. 补齐工具：fs 其余能力 → shell（含危险命令禁止清单）→ web
  - [x] 12a. `fs_list_dir` / `fs_search`（含噪声目录剪枝）
  - [x] 12b. `shell_run`（三档判定：放行 / 审批 / 禁止）
- [x] 13. `ohopilot/skills/loader.py` —— 技能系统
- [x] 14. `ohopilot/runtime.py` —— 装配层（出现第二个客户端时才需要它）
  - 边界：Runtime 管「一轮问答怎么编排」（压缩摘要 / 跑 agent / 双写存储），入口只管「怎么显示」和「怎么问人」（approver 传进来）
  - 新增 `notice` 事件：给界面的提示（如「已压缩成摘要」），不落库、不进 prompt
- [x] 15. `ohopilot/server/app.py` —— HTTP + SSE 服务端（为桌面端 / 手机端铺路）
  - 端点：`GET /health`、`GET /sessions`、`GET /sessions/{id}`、`POST /chat`(SSE)、`POST /approvals/{call_id}`
  - 审批从终端 y/n 换成 `ApprovalBroker`：Future 挂起协程 + HTTP 回调唤醒，超时按拒绝
  - `Approver` 签名补上 `call_id`（终端无所谓，HTTP 必须能对应是哪次调用）
  - 一个长驻事件循环（Runtime 在 lifespan 建/关）+ 每会话一把锁（同会话请求串行）
- [ ] 16. 测试与文档

## 参考实现在哪

完整可运行的「参考答案」保存在两处，任何时候都能查：

- GitHub 标签：`v1-skeleton`
- 本地副本：`~/Desktop/AI软件开发/Agent/OhoPilot-v1-参考/`

建议：先自己写，卡住了再对照。不要一上来就抄。

> ⚠️ 注意：v1 参考副本里的**工具名用了点号**（`fs.read_file`、`shell.run`…），
> 这会被 DeepSeek / OpenAI 的接口拒绝（见下方命名规范）。对照时**别照抄名字**。

## 工具命名规范（第 7 步踩到的真实坑）

模型接口对工具名有硬性限制：

```
^[a-zA-Z0-9_-]+$      ← 只允许 字母、数字、下划线、连字符
```

**点号（`.`）、冒号、中文、空格一律非法**，会被接口直接拒绝：

```
Invalid 'tools[0].function.name': string does not match pattern
```

所以本项目统一用**下划线**做命名空间分隔：

| 用途 | 正确 | 错误 |
|---|---|---|
| 读文件 | `fs_read_file` | ~~`fs.read_file`~~ |
| 写文件 | `fs_write_file` | ~~`fs.write_file`~~ |
| 列目录 | `fs_list_dir` | ~~`fs.list_dir`~~ |
| 搜文件 | `fs_search_files` | ~~`fs.search_files`~~ |
| 执行命令 | `shell_run` | ~~`shell.run`~~ |
| 抓网页 | `web_fetch` | ~~`web.fetch`~~ |
| 搜网页 | `web_search` | ~~`web.search`~~ |

## 环境坑：`oho` 报 `ModuleNotFoundError: No module named 'ohopilot'`（已解决）

**现象**：`cd` 进项目后 `.venv/bin/oho "..."` 报找不到包，但 `python -c "import ohopilot"` 却正常。

**为什么会这样**：
- `oho` 是 pip 生成的启动器，内容就一句 `from ohopilot.cli import main`；
- 它靠 editable 安装留在 `site-packages/_editable_impl_ohopilot.pth` 里的**路径指针**找到源码；
- `python -c` / `python -m` 会把**当前目录**放进 `sys.path`，而 `.venv/bin/oho` 的 `sys.path[0]` 是 `.venv/bin`——所以两者处境不同。

**根因**：CPython 3.13 在 `site.py` 里新增了逻辑——**跳过带"隐藏"标志的 `.pth` 文件**（`UF_HIDDEN`）。这台机器上该 `.pth` 被系统打上了 `hidden` 标志，于是指针被静默忽略。

用 `ls -lO .venv/lib/python3.13/site-packages/*.pth` 可以亲眼看到那个 `hidden`。

**当前修复**：在 `site-packages/sitecustomize.py` 里把项目根补进 `sys.path`。
`sitecustomize` 是 Python 启动时**按名字 import** 的，不看文件标志，所以稳定生效。

**更"正统"的替代做法**（随时可切）：

```bash
chflags nohidden .venv/lib/python3.13/site-packages/*.pth
rm .venv/lib/python3.13/site-packages/sitecustomize.py
```

若 `hidden` 标志反复出现（本机确实会），就保留 `sitecustomize.py`——它不是黑魔法，只是"环境补丁"。

**注意**：重建 `.venv` 后，这个补丁会消失。届时要么再跑一遍上面的 `chflags`，要么重新写 `sitecustomize.py`。
