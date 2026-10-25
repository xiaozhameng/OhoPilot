# OhoPilot 路线图 v2 —— 办公助手

> 这是我们的进度清单，**不是代码**。
>
> **协作方式（2026-10-21 起）**：代码由我直接写进项目，每一步按
> 「为什么做 → 框架长什么样 → 逐处解释代码 → 拆解验收命令」四拍交付，再带你运行调试。
> **开始写下一步之前**，先把上一步 commit + push（新代码留在工作区方便 IDE 看差异）。

## 最终愿景

OhoPilot = 桌面优先的**办公助手**：

- **能读**——你的文件（含 PDF/Office）、你的个人知识库、公开网页
- **能做**——查资料、写文件、跑命令，危险动作必经人工审批
- **能管**——多步任务自己拆解、自己勾进度，不跑偏
- **可控可审计**——每一轮 token 花在哪、每次工具调了什么，开发者控制台看得见
- **可扩展**——MCP 协议接入社区工具生态，技能市场装别人的技能
- **可交付**——Tauri 桌面壳，双击即用

## 前置条件

- DeepSeek API Key（`.env` 的 `OHOPILOT_API_KEY=`）
- `.venv`（Python 3.13，已装好）；网页版 `./start.sh` 一键起

---

## 已完成（v1 存档，详细记录见 git 历史）

- **阶段一**（步 1–9）：最小内核——项目骨架 / config / events / 工具模具 / fs / llm(LiteLLM) / agent 主循环 / CLI
- **阶段二**（步 10–18）：
  - 10 主循环加厚（多轮工具/轮次上限/审批）；11 会话库 + 长期记忆 + 滚动摘要
  - 12 补齐工具（fs 全套 / shell 三档判定 / web_fetch SSRF 防护）；13 技能渐进披露
  - 14 Runtime 装配层；15 HTTP+SSE 服务端（ApprovalBroker 挂起式审批）
  - 16 测试（218 用例）+ README；17 网页工作台（React+TS+Vite）；18 Token 可观测面板
- 检查点：`b931afd`（16/17 步 + start.sh 已推送）；第 18 步未提交

### 存档里的关键教训（别再踩）

**工具命名**：模型接口只允许 `^[a-zA-Z0-9_-]+$`，点号/冒号/中文一律拒绝。本项目统一下划线分命名空间（`fs_read_file`、`web_search`）。

**macOS hidden .pth 坑**：CPython 3.13 跳过带 hidden 标志的 `.pth`，导致 `oho` 报 ModuleNotFoundError。当前靠 `sitecustomize.py` 补 sys.path；重建 `.venv` 后补丁会消失，需重做（详见 git 历史 ROADMAP v1）。

---

## 阶段三：办公助手雏形（当前阶段）

- [x] **19. web_search 工具**（搜索：让 agent 能查公开信息）
  - 一个 `web_search` 工具，三个 provider 后端可切换（`OHOPILOT_SEARCH_PROVIDER`）：
    `searxng`（自建 Docker，免费，默认）｜`tavily`（免费 1000 次/月，需代理）｜`bocha`（付费 ≈0.03 元/次，中文最佳）
  - 结果截断进上下文（标题+摘要+链接，≤N 条），防撑爆
  - 技术选型对比表汇总进 README（用户要求：所有外部资源选型必须进文档）
- [x] **20. 任务清单 / 规划**（规划层第一块砖：长任务不迷路）
  - `todo_write` / `todo_update` 工具：agent 自己维护任务列表并勾选
  - 计划-执行两段式：复杂任务先列计划（可审批）再逐项执行
  - 事件协议扩展：`todo` 事件，网页端常驻任务面板
- [x] **21. 文档解析**（办公助手的基本盘：能读 PDF / Office）
  - `fs_read_document`：PDF（pypdf）、docx、xlsx → 纯文本，分页/分表截断
  - 归入可选依赖组 `[docs]`
  - 追加：网页 📎 上传入口（POST /files → `~/.ohopilot/uploads/`，重名不覆盖）
- [x] **22. 个人知识库 RAG**（语义找回：文档大了以后按需检索）✅ 2026-10-25
  - **向量库接口 + 双实现**（用户拍板）：`VectorStore` 统一接口（add/search/delete），
    `sqlite-vec` 适配器（默认，零服务）+ `Milvus` 适配器（用户本地 Docker 已装），
    `OHOPILOT_VECTOR_BACKEND=sqlite-vec|milvus` 一键切换
  - 索引管线：`kb_index` 工具切块 → embedding → 入库；`kb_search` 工具给 LLM 按需检索
  - embedding 走 OpenAI 兼容接口（可配）
  - 依赖组 `[rag]`；install.sh 检测两种后端（Milvus 只在选它时检查连通性）

## 阶段四：可扩展架构

- [x] **23. MCP 客户端（stdio）**（动态接入外部工具生态，架构级改动）✅ 2026-10-25
  - MCP = 标准协议：外部工具跑在独立进程，宿主启动时连接、拉取工具清单
  - 核心改动：`ToolRegistry` 从「启动固定 8 个」改为「内置 + 动态注册」两层
  - MCP 工具 schema ↔ 本项目 `Tool` 模具的转换层；工具调用跨进程转发
  - 配置：`~/.ohopilot/mcp.json`（对标业界惯例）；范围先 stdio，HTTP 后补
- [x] **24. 技能中心**（安装别人的 skill）✅ 2026-10-25
  - `skill_install` 工具：本地目录（Claude 形态）/ URL 两种来源
  - 格式兼容层：Claude 系 frontmatter 转换（名字归一化、多余字段丢弃、description 兜底）
  - 安装前安全审计：danger（sudo/rm -rf/下载即执行等）无条件拒装；warning（联网/装包等）需 confirm_risky 确认
- [x] **25. 模型路由**（省钱：简单步骤走小模型）✅ 2026-10-25
  - `OHOPILOT_FAST_MODEL` 配置后台任务用的便宜模型；不配 = 不路由，行为不变
  - 滚动摘要（最典型的后台杂活）已路由；`complete(fast=True)` 是统一入口
  - 用第 18 步面板量化路由前后的成本对比（llm_calls 表已记 model 列，可直接 GROUP BY 对比）

## 阶段五：运营与交付

- [x] **26. 开发者控制台**（独立入口，用户拍板：同服务 /console 路径）✅ 2026-10-25
  - 后端 `/console/overview`：token 总账、按模型拆分（路由省钱效果在这看）、工具调用排行+错误率、每会话成本 Top 20
  - 前端第二个页面挂 `/console`（同一 SPA 按 pathname 分流），主界面顶栏「控制台」跳转
  - 新增 `tool_calls` 流水表（Runtime 在 tool_result 时落库，agent 保持零存储依赖）
  - 单用户阶段不拆服务、不做鉴权；多用户时按 /console/* 前缀整体迁走
- [x] **27. Tauri 桌面壳**（复用 web/ 前端，打包成 .app）✅ 脚手架 2026-10-25
  - `src-tauri/`：Tauri 2 配置 + 壳进程（原生窗口加载本机服务 URL，同源零 CORS）
  - 界面零改动（复用 web/ 产物）；打包需 Rust 工具链，步骤见 `src-tauri/README.md`
- [x] **28. 打包分发 + install.sh** ✅ 2026-10-25
  - install.sh：找 Python（3.12+）→ 建 venv → 装依赖（`--core` 可选；失败自动换清华镜像）→ 生成 .env → 检查前端产物 → 装配自检（`--with-tests` 加跑 pytest），幂等可重复执行
  - README「依赖矩阵」表 + 快速开始改为 install.sh 优先

## 里程碑记录（借鉴 commit 区间口径）

> 口径来源：某个 RL 数据平台的仓库质量标准（2026-10-25 用户提供）——
> 每个里程碑记 **起始 commit + 结束 commit + 备注**（本轮目的是什么、主要实现了什么、
> 为什么这几个 commit 合成一个区间）。好处：任何一段开发都能顺着区间在 git 历史里完整回放。

| 里程碑 | 起始 | 结束 | 备注（目的 / 主要实现 / 为什么合成一个区间） |
|---|---|---|---|
| M0 · v1 参考版 | `7437896` | `eaee071` | 第一版完整可运行实现，tag `v1-skeleton`。仅作参考答案——工具名带点号，接口会拒绝，别照抄 |
| M1 · 内核与记忆（v0.1） | `6f36b70` | `8f31f6e` | 目的：从零重建并加厚到「单机能用」。步 1–13：主循环、会话库+长期记忆+滚动摘要、fs/shell/web 工具、技能系统。合为一个区间：都是「把骨架立起来」的连续开发，步 1–9 的文件首次随 65393f7 入库 |
| M2 · 服务化与产品化（v0.1） | `3ea5f41` | `b931afd` | 目的：从「能跑」到「像产品」。步 14–17：Runtime 装配层、HTTP+SSE 服务端、网页工作台、207 测试、README、start.sh。合为一个区间：服务端→界面→测试→文档是一条交付链 |
| M3 · 可观测（v0.2） | `b931afd` | `3836e08` | 第 18 步：每轮用量/缓存命中/耗时落库 + 开发者面板，218 用例。目的是「优化之前先有仪表盘」。单 commit 区间：仪表盘是一条完整交付链 |
| M4 · 办公助手雏形（v0.3） | `ae13c53` | `8d598f5` | 步 19–22：web_search / 任务清单 / 文档解析+上传 / RAG（含 uploads 搬家、家目录写边界两个拍板）。每个工具一个 commit，最后 8d598f5 收口 RAG |
| M5 · 可扩展与交付（v0.4） | `44a1bca` | 本轮收口 commit | 步 23–28：MCP / 技能中心 / 模型路由 / 控制台 / Tauri 脚手架 / install.sh。ROADMAP v2 的 28 步到此全部完成 |

## 已定技术决策（2026-10-25 用户拍板）

| 决策点 | 结论 |
|---|---|
| 向量库 | 接口 + 双实现：sqlite-vec 默认，Milvus 可切换（本地 Docker 已装） |
| 搜索 API | provider 可切换：searxng（默认免费）/ tavily（免费额度）/ bocha（付费）；选型表进 README |
| MCP 范围 | 第一版只做 stdio |
| 开发者控制台 | 同服务 /console 路径，接口按 /console/* 分组预留拆分 |
| 依赖哲学 | 核心零外部服务；重依赖全走可选安装组（`[rag]` `[docs]` …） |
| 目录分工 | `~/.ohopilot` = 运行时数据（db/技能/记忆/上传文件）；workspace = agent 写出的结果；安装目录 = 纯代码 |
| 写边界 | 可写范围 = workspace ∪ 数据家目录；家目录下 `ohopilot.db` / `MEMORY.md` 拒绝裸写（各有专用通道）。将来支持**会话级工作目录**（用户指定）时沿用此模型 |

## 参考实现在哪

- GitHub 标签：`v1-skeleton`；本地副本：`~/Desktop/AI软件开发/Agent/OhoPilot-v1-参考/`
- ⚠️ v1 里工具名带点号（`fs.read_file`），接口会拒绝，对照时别照抄名字
