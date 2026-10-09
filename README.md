# OhoPilot

> 跑在你电脑上、会自己长技能的中文办公 agent。
> 中文名「哦豁」——`oho` 在英语里是"啊哈，原来如此"，在芬兰语里是"哎呀"。

这是一个 **client-server 架构**的 agent 内核。第一天就这么分，是为了让你将来做手机端时不用重写：

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  CLI (今天)   │   │ 桌面 App(将来) │   │ 手机 App(更远) │
└──────┬───────┘   └──────┬───────┘   └──────┬───────┘
       └──────────────────┼──────────────────┘
                          ▼
              ┌───────────────────────┐
              │  内核 ohopilot.core    │   一个循环：模型 ⇄ 工具
              │  ├─ tools   工具系统   │
              │  ├─ memory  记忆       │
              │  ├─ skills  技能       │
              │  └─ server  HTTP/SSE   │
              └───────────────────────┘
```

**内核完全不知道前端长什么样。** 所有输出都是统一的事件流（`core/events.py`），
CLI 和 HTTP 服务端只是两个不同的消费者。

---

## 快速开始

```bash
cd ~/Desktop/AI软件开发/Agent/OhoPilot

# 1. 建虚拟环境
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. 配密钥
cp .env.example .env
# 编辑 .env，填入 OHOPILOT_API_KEY

# 3. 体检
oho doctor

# 4. 开始用
oho                              # 交互式
oho "看看当前目录有哪些文件"        # 带提示词直接开始
oho run "把 README 翻译成英文"     # 非交互，跑完即退
oho serve                        # 起 HTTP 服务端
```

默认接 DeepSeek（`openai/deepseek-v4-flash` + `https://api.deepseek.com/v1`）。
换模型只改 `.env` 里一行 `OHOPILOT_MODEL`，代码不用动。

---

## 目录说明

```
ohopilot/
├── config.py           配置收拢：环境变量 / .env
├── runtime.py          运行时装配：把下面这些拼成一个 agent
├── cli.py              命令行界面（内核的客户端 A）
├── core/
│   ├── llm.py          模型层：LiteLLM 统一各家接口，流式
│   ├── agent.py        ★ 主循环就在这里，整个软件的心脏
│   └── events.py       内核对外的事件协议
├── tools/
│   ├── base.py         工具协议 + 注册表
│   ├── fs.py           读 / 写 / 列目录 / 搜文件
│   ├── shell.py        执行命令（含危险命令禁止清单）
│   └── web.py          抓网页 / 搜网页
├── memory/store.py     会话历史（SQLite）+ 长期记忆（MEMORY.md）
├── skills/loader.py    技能系统：渐进式披露
└── server/app.py       HTTP + SSE（内核的客户端 B）
```

---

## 三个设计要点

**1. 主循环只有 4 个动作**（`core/agent.py`）
把上下文发给模型 → 模型要工具 → 确认后执行 → 结果回填，再问一次。
读懂了它，你就理解了所有 agent。

**2. 记忆分两级，别一上来上向量库**
- 会话历史：SQLite，超过 60 条自动让模型压成摘要
- 长期记忆：`~/.ohopilot/MEMORY.md`，每次启动整份进系统提示词，用户可直接手改

**3. 渐进式披露的技能系统**
启动时只把技能的 `name + description` 塞进提示词（几十字），模型判断需要时才读全文。
技能可以无限多，上下文不会爆。往 `~/.ohopilot/skills/` 丢一个 `.md` 就是一个新技能：

```markdown
---
name: my-skill
description: 一句话说清什么时候该用它（模型靠这句选技能）
---

# 具体步骤
1. ...
```

---

## 安全边界

| 层级 | 做法 |
|---|---|
| 硬禁止 | `rm -rf`、`mkfs`、`dd of=/dev/`、`curl \| sh`、关机等，直接拒绝，连确认机会都不给 |
| 逐条确认 | `shell.run` 的所有其他命令 |
| 越界确认 | 文件读写落在工作目录之外时 |
| 用户可查 | `oho doctor` 一眼看全当前配置与工具 |

工作目录之外的写入、以及所有命令执行，默认都要人工点一次确认。

---

## 当前进度（MVP 已完成的）

- [x] 内核主循环（流式 + 工具调用 + 多轮）
- [x] 工具系统：8 个内置工具 + schema 自动生成
- [x] 记忆：SQLite 会话 + 超长自动压缩 + 长期记忆文件
- [x] 技能系统：渐进式披露
- [x] CLI 客户端
- [x] HTTP/SSE 服务端（手机端接入的前置）
- [x] 安全闸：禁止清单 + 分级审批
- [x] 冒烟测试

## 下一步

- [ ] 桌面壳（Tauri 2）
- [ ] 技能自动生成（从成功任务里沉淀）
- [ ] 定时任务（cron）
- [ ] MCP 客户端接入
- [ ] 手机端遥控

---

## 开发

```bash
python -m pytest -q          # 跑测试（不联网、不花钱）
```
