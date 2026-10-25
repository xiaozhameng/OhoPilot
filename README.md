# OhoPilot（哦豁）

跑在你自己电脑上的中文办公 agent。它读写你的文件、执行命令、抓网页，
危险动作先问你一句；对话存在本地 SQLite，重启能接着聊；技能是可编辑的 Markdown 文件。

不是通用聊天壳子：它只做「拿你电脑上的东西办事」这一类活。

## 它有什么

- **14 个内置工具**：读/写/列目录/搜索、读 PDF/Word/Excel、执行 shell、抓网页、**搜网页**、写长期记忆、取技能正文、任务清单（todo）、知识库索引/语义检索（kb）。
- **三档 shell 判定**：只读命令静默执行，改动类弹审批，提权删根类直接拒绝。
- **三层安全边界**：工作目录硬边界 → 硬禁止清单（`.env`/`.git`/凭据目录）→ 人工审批。
- **两层记忆**：会话历史（SQLite，滚动摘要压缩）+ 长期记忆（`MEMORY.md`，注入每轮提示词）。
- **技能系统**：`~/.ohopilot/skills/*.md`，系统提示里只放目录，按需取正文（渐进披露）。
- **两个客户端 + 一个网页工作台**：终端 CLI、HTTP + SSE 服务端，以及一个对标豆包/WorkBuddy 的网页界面（React + TS + Vite，构建产物由 `oho-server` 直接托管，**不需要装 Node 也能用**）。
- **内核与界面解耦**：三者只通过 8 类事件通信，换界面不动内核。

## 快速开始

```bash
cd ~/Desktop/AI软件开发/Agent/OhoPilot
source .venv/bin/activate
pip install -e ".[dev,server]"
cp .env.example .env
```

**可选依赖组**：核心功能零额外依赖；用到对应功能再装，缺库时工具会报错并提示安装命令。

| 组 | 装法 | 解锁的功能 |
|---|---|---|
| `server` | 已含在快速开始里 | HTTP 服务 + 网页工作台 |
| `docs` | `pip install "ohopilot[docs]"` | `fs_read_document` 读 PDF / Word / Excel（三个解析库共约 40MB） |
| `rag` | `pip install "ohopilot[rag]"` | 知识库 `kb_index` / `kb_search`（另需配 embedding，见 `.env.example`；sqlite-vec 零服务默认，Milvus 可切） |
| `dev` | `pip install "ohopilot[dev]"` | 跑测试 |

> 小提示：`.env` 里加 `LITELLM_LOCAL_MODEL_COST_MAP=True` 可以跳过 litellm 启动时联网拉模型价格表（国内网络会白等约 3 秒）。

打开 `.env` 填上 `OHOPILOT_API_KEY`，然后一条命令起网页版：

```bash
./start.sh
```

浏览器会自动打开 `http://127.0.0.1:8737`。**服务在前台阻塞，Ctrl+C 或关掉这个终端窗口就停**，不会留后台进程。

脚本做三件事：检查环境（虚拟环境、`fastapi`、`.env`、端口占用）→ 缺前端产物就构建 → 前台启动。缺什么当场报明确的错。

| 命令 | 作用 |
|---|---|
| `./start.sh` | 网页版（API + 界面同端口） |
| `./start.sh --dev` | 开发模式：后端 + vite 热更新，改 `web/src` 即时生效 |
| `./start.sh --rebuild` | 改了前端但没构建时，强制重新 `npm run build` |
| `./start.sh --port 9000` | 临时换端口，不改 `.env` |
| `./start.sh --no-open` | 不自动开浏览器 |

终端版另有一个入口（`./start.sh` 起的是服务，不起终端界面）：

```bash
oho
```

进入交互模式，直接打字提问。内置命令：`/new` 开新会话，`/exit` 退出。

一次性提问（适合写脚本）：

```bash
oho "把当前目录的 py 文件按行数排序"
```

## 三种使用方式

### 1. 终端交互

每次启动自动接续最近一个会话；`/new` 之后旧会话仍留在库里。

### 2. 终端一次性提问

带参数就答一次然后退出，每次开新会话、不带上下文。

### 3. HTTP + SSE 服务端

```bash
oho-server
```

默认监听 `127.0.0.1:8737`（只绑本机，不要对外暴露——它能执行 shell）。

健康检查：

```bash
curl --noproxy '*' http://127.0.0.1:8737/health
```

> `--noproxy '*'` 是给开了系统代理的机器用的。不加的话 curl 会把本机请求也丢给代理，直接连不上。

问一轮，流式读事件：

```bash
curl --noproxy '*' -N -X POST http://127.0.0.1:8737/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"你好"}'
```

每行是 `data: {"type": "...", ...}`，事件类型见下表。新会话的 id 在响应头 `X-Session-Id` 里；
接续会话就把它填回 `session_id`。

审批回传（客户端收到 `approval_request` 事件后）：

```bash
curl --noproxy '*' -X POST http://127.0.0.1:8737/approvals/<call_id> \
  -H 'Content-Type: application/json' -d '{"allow":true}'
```

其他端点：`GET /sessions`（会话列表，带标题与条数）、`GET /sessions/{id}`（回放历史）、`POST /settings`（改模型与工作目录）。

## 网页工作台

`oho-server` 起来之后，浏览器打开 `http://127.0.0.1:8737` 就是界面——左侧会话列表、中间对话流（工具调用是可折叠的卡片，点开能看完整参数与返回）、底部输入框、危险操作时审批卡片浮在输入框上方。设置抽屉里可以在线换模型和工作目录，不用重启。

界面是 React + TypeScript + Vite 写的，源码在 `web/`。**仓库里已经包含构建产物**（`ohopilot/server/static/`），所以只用 Python 的同学不需要装 Node。

想改界面时才需要前端工具链：

```bash
cd web
npm install
npm run dev
```

- `npm run dev`：起 5173 端口的开发服务器，改动即时热更新；API 请求由 vite 转发到 8737（要先把 `oho-server` 跑起来）
- `npm run build`：类型检查 + 构建，产物直接落到 `ohopilot/server/static/`，之后 `oho-server` 打开的就是新界面

前端有几个值得知道的实现细节（都在源码注释里展开写过）：

1. **不用 `EventSource`**——它只能发 GET、不能带请求体。`web/src/api.ts` 用 `fetch` + `ReadableStream` 手写 SSE 解析。
2. **一条助手消息是有序块列表**（文本/工具/提示/错误），不是一条字符串——工具调用会插在文本流中间，拼成字符串就再也分不清顺序。
3. **审批是反向请求**：SSE 是单向的，前端把 `approval_request` 排成队列弹卡片，点完再 `POST /approvals/{call_id}` 唤醒后端挂起的协程。
4. 会话支持 `?session=<id>` 深链，刷新页面不掉上下文；`?usage=1` 直接打开用量面板。

### 开发者面板：token 花在哪

顶栏的图表图标（或 `?usage=1`）打开用量面板，数据来自 `GET /usage`：

- **总账卡片**：调用次数、总 tokens、**缓存命中率**、平均首字延迟；
- **流水表**：每次模型调用一行——第几轮、prompt / 命中 / 未命中 / 输出各多少、耗时、按前端价格表估算的成本。

为什么命中率是最重要的数字：DeepSeek 对命中前缀缓存的部分按约 1/10 计价。命中率低通常说明每次请求的前缀在变（比如系统提示里混入了时间戳），修它就是省钱。命中率低于 50% 时面板会直接给出这条提示。

实现链路：`llm.py` 挖出各家 API 的缓存字段 → `agent.py` 每轮发 `usage` 事件（带轮次号与上下文构成）→ `runtime.py` 落库到 SQLite 的 `llm_calls` 表 → `/usage` 出数据。主循环自身不碰存储。

## 一次问答发生了什么

```
你 ──"整理下项目里的 TODO"──▶ Runtime.ask()
                                 │
                                 ├─ ① 未压缩消息超阈值？→ 压一批成摘要，history 换成「摘要+最近几条」
                                 │
                                 ├─ ② Agent.run()：组系统提示（身份/目录/工具/长期记忆/技能目录/规则）
                                 │        │
                                 │        ├─ ③ stream_chat 流式要模型 → 边收边吐 text 事件
                                 │        │
                                 │        ├─ ④ 模型要调工具 → tool_call 事件
                                 │        │      ├─ 要审批？→ approval_request 事件 → 等人回答
                                 │        │      └─ safe_run（兜异常 + 截断结果）→ tool_result 事件
                                 │        │
                                 │        └─ ⑤ 结果回填进消息，回到 ③（最多 max_tool_rounds 轮）
                                 │
                                 └─ ⑥ 双写：内存 history（进下轮 prompt）+ SQLite（进硬盘）
                                          → done 事件
```

关键点：**内核不知道自己在哪个会话里**，也从不 print。会话 id 由入口给，
显示由入口做——所以终端和 HTTP 能共用同一个 `Runtime.ask()`。

## 事件协议

| type | 什么时候发 | 关键字段 |
|---|---|---|
| `text` | 模型输出增量 | `delta` |
| `tool_call` | 模型决定调工具 | `id` `name` `arguments` |
| `tool_result` | 工具执行完 | `id` `name` `content` `is_error` |
| `approval_request` | 需要人工确认 | `id` `name` `arguments` `reason` |
| `usage` | 一轮模型调用结束 | `prompt_tokens` `completion_tokens` `total_tokens` |
| `notice` | 给界面的提示（如已压缩） | `message` |
| `error` | 出错 | `message` |
| `done` | 本轮结束 | `session_id` |

`notice` 单独一类而不是塞进 `text`：`text` 会被当成模型输出存进历史，
提示信息不该落库、也不该影响下一轮 prompt。

## 工具清单

| 工具 | 作用 | 审批 |
|---|---|---|
| `fs_read_file` | 带行号读文本，支持 offset/limit | 否 |
| `fs_write_file` | 整文件覆盖写，父目录自动建 | 是 |
| `fs_list_dir` | 列一层目录，目录带 `/`，文件带字节数 | 否 |
| `fs_search` | 文件名 + 内容搜索，剪掉噪声目录 | 否 |
| `shell_run` | 执行 shell 命令 | 三档 |
| `web_fetch` | 抓网页转纯文本（挡 SSRF） | 否 |
| `web_search` | 关键词搜网页，返回标题+链接+摘要（后端可切） | 否 |
| `memory_append` | 往长期记忆追加一条 | 是 |
| `skill_load` | 取某个技能的正文 | 否 |

`shell_run` 的三档：

- **allow**：命令在只读白名单里，**且**不含 shell 元字符（`; & | > < \` $ 换行`）→ 静默执行。
  两个条件缺一不可：`ls; rm -rf ~` 的第一个词也是 `ls`。
- **ask**：其余一切（含管道、重定向、串联、越出工作目录的路径）→ 弹审批。
- **block**：提权、删根目录、磁盘操作、下载即执行、碰 `.env` 与凭据目录 → 直接拒绝，连审批都不给。

引号里的内容先被抹掉再判断元字符——否则 `git log --pretty=format:"%ad | %s"` 会被误判成管道。
但 `block` 那一档拿原文匹配：宁可误杀，不可漏放。

## 安全边界

四层，从内到外：

1. **工作目录**：文件工具的路径 `resolve()` 后必须落在 `workspace` 内，否则拒绝。
2. **硬禁止清单**：路径里出现 `.env` / `.git` 一律拒绝，读写都拒。读 `.env` 会把密钥送进模型对话，和写坏它一样严重。
3. **人工审批**：写文件、写长期记忆、疑似危险的 shell 命令，都要用户点头。没人能批准时（比如管道里跑）一律按拒绝处理。
4. **子进程环境剥离**：`shell_run` 起的子进程拿不到任何名字里带 `KEY`/`TOKEN`/`SECRET`/`PASSWORD` 的变量，也拿不到 `OHOPILOT_*` 配置——否则模型执行一次 `env` 就能把 key 读走。

另外：`web_fetch` 在发请求**之前**做 SSRF 校验，先挡本地名，再把域名解析成 IP 判断是否内网/回环/链路本地（云厂商元数据地址 `169.254.169.254` 就在这一档），所以 `127.0.0.1.nip.io` 这种伪装域名也过不去。

## 目录结构

```
ohopilot/
├── config.py            统一读环境变量与 .env，别处不再碰 os.environ
├── core/
│   ├── events.py        内核与界面的词汇表
│   ├── llm.py           LiteLLM 薄封装：stream_chat + complete
│   └── agent.py         主循环（心脏）
├── tools/
│   ├── base.py          工具模具 + 注册表 + 结果截断
│   ├── fs.py            读/写/列/搜
│   ├── shell.py         三档判定 + 进程组超时
│   ├── web.py           SSRF 防线 + HTML 提取
│   ├── memory.py        长期记忆写入
│   └── skills.py        技能正文读取
├── memory/
│   ├── store.py         SQLite 会话库（历史 + 摘要）
│   ├── longterm.py      MEMORY.md 读写
│   └── summarize.py     滚动摘要
├── skills/loader.py     Markdown 技能解析
├── runtime.py           装配 + 一轮问答编排
├── cli.py               终端入口（只做渲染与 y/n）
├── server/
│   ├── app.py           FastAPI：/health /sessions /chat(SSE) /approvals /settings + 前端托管
│   ├── approvals.py     ApprovalBroker：Future 挂起 + HTTP 唤醒
│   └── static/          网页工作台的构建产物（源码在 web/，入库使克隆即用）
└── ../web/              前端源码（React + TS + Vite，与 python 包平级）
    └── src/
        ├── api.ts       fetch + ReadableStream 手写 SSE 客户端
        ├── state.ts     对话状态机（reducer + applyEvent 纯函数）
        ├── types.ts     与 core/events.py 一一对应的「合同」
        └── components/  Sidebar / Topbar / MessageList / ToolCard / Composer / ApprovalBar / Drawer
```

运行时数据在 `~/.ohopilot/`：`ohopilot.db`（会话）、`MEMORY.md`（长期记忆）、`skills/`（技能）。

## 配置

全部走环境变量（或 `.env`），前缀 `OHOPILOT_`。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `OHOPILOT_API_KEY` | 空 | 模型密钥；为空时 CLI 直接退出，服务端 `/chat` 返回 503 |
| `OHOPILOT_MODEL` | `deepseek-v4-flash` | 换模型只改这里 |
| `OHOPILOT_API_BASE` | `https://api.deepseek.com` | 指向本地模型（Ollama/vLLM）时可免 key |
| `OHOPILOT_WORKSPACE` | 当前目录 | 文件工具的硬边界 |
| `OHOPILOT_HOME` | `~/.ohopilot` | 数据目录 |
| `OHOPILOT_ALLOW_WRITE` | `1` | 文件写入总开关；设 `0` 变只读模式（`fs_write_file` 一律拒绝） |
| `OHOPILOT_SHELL_TIMEOUT` | `60` | 单条命令上限秒数 |
| `OHOPILOT_MAX_TOOL_ROUNDS` | `12` | 一轮问答里最多几次工具往返 |
| `OHOPILOT_SUMMARY_AFTER` | `24` | 未压缩消息超过这个数就压一批；`0` 关闭摘要 |
| `OHOPILOT_SUMMARY_KEEP` | `8` | 压缩后始终保留原文的最近条数 |
| `OHOPILOT_HOST` / `OHOPILOT_PORT` | `127.0.0.1` / `8737` | 服务端监听地址 |
| `OHOPILOT_APPROVAL_TIMEOUT` | `180` | HTTP 审批等待上限，超时按拒绝 |
| `OHOPILOT_SEARCH_PROVIDER` | `searxng` | 网页搜索后端：`searxng` / `tavily` / `bocha` |
| `OHOPILOT_SEARXNG_BASE_URL` | `http://127.0.0.1:8080` | 自建 SearXNG 地址 |
| `OHOPILOT_TAVILY_API_KEY` | 空 | provider 为 tavily 时必填 |
| `OHOPILOT_BOCHA_API_KEY` | 空 | provider 为 bocha 时必填 |

## 网页搜索：技术选型

`web_search` 的后端可切换（改 `OHOPILOT_SEARCH_PROVIDER`），三家对比：

| 后端 | 费用 | 中文效果 | 访问 | 适合 |
|---|---|---|---|---|
| **searxng**（默认） | 完全免费（自建） | 中等，取决于聚合的引擎 | 本机直连 | 日常默认，零成本无限量 |
| **tavily** | 免费 1000 次/月，之后付费 | 中上 | 国内常需代理 | Agent 生态标配，结果结构化好 |
| **bocha** | ≈0.03 元/次 | 最好 | 国内直连 | 要高质量中文结果时 |

**后端只是配置，没有写死**：三个后端对应同一套工具代码，切换 = 改 `.env` 里一行 `OHOPILOT_SEARCH_PROVIDER`（用 tavily/bocha 时另配对应 key），代码零改动。

**免费与代价要一并看**：searxng 完全免费、无次数限制，但有两个代价——① 稳定性靠自己：上游搜索引擎会反爬，某几家抓不到时要等社区跟进；② 返回的是原始搜索结果（标题+链接+摘要），噪音比商业 API 多一点。tavily 则替你扛了反爬和结果清洗，免费额度 1000 次/月对个人够用。

**推荐用法**：开发调试用 `searxng`（免费随便造），日常正式使用或演示切 `tavily`（质量稳，额度够）。

**SearXNG 自建**（默认后端）两步：

```bash
docker run -d --name searxng -p 8080:8080 searxng/searxng
docker exec searxng sh -c \
  "sed -i 's/formats: - html/formats: - html\n    - json/' /etc/searxng/settings.yml && docker restart searxng"
```

- 第一行：起服务，映射到本机 8080
- 第二行：SearXNG **默认关闭 JSON 输出**（反爬措施），而我们的工具走 `format=json`，必须打开；改完重启生效
- 验证：`curl --noproxy '*' 'http://127.0.0.1:8080/search?q=test&format=json'` 返回 JSON 即成功

安全设计：搜索端点全部来自配置，模型只能控制关键词——「去哪查」它说了不算，所以不像 `web_fetch` 那样需要 SSRF 防护。本机 searxng 请求固定绕过系统代理（`trust_env=False`），公网 API 保留代理。

## 写一个技能

技能是 Markdown，一个文件一个技能，放在 `~/.ohopilot/skills/`：

```markdown
---
name: weekly-report
description: 生成周报。用户提到周报、本周总结、这周做了什么时使用。
---
1. 用 shell_run 跑 `git log --since="7 days ago" --oneline`
2. 按项目分组，每组挑 3 条有信息量的提交
3. 输出：本周完成 / 进行中 / 下周计划，每项不超过两行
```

规则（都写在 `skills/loader.py` 里）：

- `description` 必填。它是模型判断「要不要用这个技能」的唯一依据，缺了整份文件被丢弃。
- 正文不能为空，否则和 `description` 重复。
- 正文上限 8000 字符，超了截断。
- `name` 只允许字母数字下划线连字符，同名只保留第一个。

为什么不做成工具：工具是**代码能力**，每加一个都占 function calling 的 schema 预算；
技能是**提示词能力**，系统提示里只占一行。加十个技能只多十行 token。

## 测试

```bash
.venv/bin/python -m pytest
```

218 个用例，分五类：

| 文件 | 覆盖什么 |
|---|---|
| `test_agent.py` | 主循环：工具往返、审批、错误、轮次上限、系统提示、usage 事件 |
| `test_server.py` | SSE 格式、同会话串行、审批全链路、Broker 超时/取消、设置与 /usage 端点 |
| `test_store.py` `test_summarize.py` `test_longterm.py` | 存储、摘要阈值与累积、记忆注入、用量流水与总账 |
| `test_llm.py` | 缓存字段的各家格式挖取（挖不到时全算未命中，不虚报命中率） |
| `test_shell.py` `test_fs_tools.py` `test_web.py` `test_skills.py` | 工具边界与安全 |
| `test_config.py` `test_cli_render.py` `test_tools_misc.py` | 配置优先级与容错、参数截断、两个薄工具 |

前端没有自动化测试（第一版刻意不做），`npm run build` 里的 `tsc --noEmit` 就是它的类型级测试。

两条纪律：**不碰真实的 `~/.ohopilot`**（每个用例用临时目录），**不发真实网络请求**（模型与下载都换成假的）。

## 已知限制

- 会话库只存 user 与最终 assistant 文本。工具往返不落库——回放一半工具轨迹会产生孤儿 `tool` 消息，模型接口会直接报错。网页版翻历史时也就看不到当时的工具卡片。
- 摘要是一次性的滚动摘要（每个会话一行），不做「摘要的摘要」的版本管理。
- 用量面板只记对话主循环的模型调用；压缩摘要那次后台调用（`complete()`）的用量没有单独记。
- 上下文构成按字符数记录，token 是近似值——模型 API 只回报总数，不回报构成。
- CLI 每问一次开一个新的事件循环；服务端是长驻循环（这是第 15 步专门解决过的坑）。
- 没有流式工具输出，工具只在跑完后一次性返回。
- 单进程、单用户。多用户与鉴权留给云服务版本。
- 改前端代码后要 `npm run build` 才会反映到 `oho-server`（开发时用 `npm run dev` 不用）。
