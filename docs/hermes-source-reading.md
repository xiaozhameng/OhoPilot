# Hermes 源码阅读对照笔记

> 目的：为 **OhoPilot** 的架构选型提供一份可长期对照的路标。不是 Hermes 的 API 文档，而是「想抄什么，去哪里看」。

**基线**

- 源码：`~/hermes-src`，`git log -1` = `0ab397fd`（main，「pm runtime lock against aliyun mirror」）
- 该目录与运行时副本 `~/.hermes/hermes-agent` **逐字节一致**（`run_agent.py` 的 sha 都是 `1f3f8022…`），只读一份。
- ⚠️ 行号会随上游演进漂移。**请用函数名定位**，文中的行号只作快速跳转的锚点（`grep -n "def xxx" 文件`）。

**规模感**：全仓 16451 个文件。硬读必崩，必须按「入口 → 装载 → 循环」三站走。

| 目录 | .py 数 | 角色 |
|---|---|---|
| `hermes_cli/` | 463 | CLI 子命令、setup、config、插件加载器、更新器 |
| `tools/` | 265 | 工具实现，靠 `tools/registry.py` 自动发现 |
| `agent/` | 248 | 轮次阶段 `turn_*.py`、provider、记忆、压缩、提示词构建 |
| `gateway/` | 123 | 多平台消息网关（run.py 门面 + run_*.py 阶段） |
| `tui_gateway/` | 79 | TUI + Desktop 的 Python JSON-RPC 后端 |
| `cron/` | 31 | 定时任务调度器 |
| 顶层 `*.py` | ~50 | `run_agent.py` / `cli.py` / `model_tools.py` / `hermes_state.py` 等门面 |

**结构总原则 —— facade + siblings（2026-09 拆分）**

曾经的「上帝文件」都被拆成 **门面**（对外入口 + 别的包 import 的名字）+ **同目录兄弟文件** `<stem>_<topic>.py`，每个兄弟只负责一个主题。

- 最大的几个家族：`hermes_state.py`（21 个兄弟）、`gateway/run.py`（15）、`tools/mcp_tool.py`（15）、`cli.py`（12 个 `hermes_cli/cli_*_mixin.py`）、`run_agent.py`（→ `agent/turn_*.py`，共 **31** 个）。
- **按主题找兄弟文件，而不是先读门面**：`grep -rn "def 名字" <目录>/<stem>_*.py`。仓库有 `evals/codebase_navigability/` 量化过「先读门面」是最贵的读法。
- 门面从不与兄弟在模块级互相 import（避免循环）；副作用是**打补丁要打在调用点读名字的那个模块**。

---

## 一、入口在哪里

入口**不在** `~/hermes-src/hermes`（那只是 262 字节的转发壳）。真正的定义在 `pyproject.toml:564` 的 `[project.scripts]`：

| 命令 | 指向 | 作用 |
|---|---|---|
| `hermes` | `hermes_cli.main:main` | **主入口**。交互 CLI / gateway / cron / doctor 全走它 |
| `hermes-agent` | `agent.legacy_cli:main` | 单次查询（`--query`），参数层再转发 `run_agent.main` |
| `hermes-acp` | `acp_adapter.entry:main` | 给 VS Code / Zed / JetBrains 的 ACP 服务 |

安装后的实际落点：`~/.local/bin/hermes` → shell 脚本 → `~/.hermes/hermes-agent/.hermes/bin/hermes`。

---

## 二、启动链：逐环对照

| # | 文件 | 锚点 | 干什么 |
|---|---|---|---|
| 1 | `hermes_bootstrap.py` | 全文 560 行 | **每个入口第一行 import**。Windows UTF-8 stdio、ANSI 控制台、`sys.path` 加固、Happy Eyeballs 双栈连接。纯 stdlib，不能引任何 Hermes 包 |
| 2 | `hermes_cli/main.py` | `def main()` **L3572**（全文 3719 行） | 分发枢纽。先处理进程标题 / 栈清理 / stdio，再走下面两条路 |
| 2a | 同上 | `_try_fast_serve_launch` **L3167**、`_try_fast_chat_launch` **L3206** | **快车道**：为省掉建 ~40 个子命令解析器的 ~140ms，对 `serve` / 裸 `hermes` 做短路。读正常流程可跳过 |
| 2b | 同上 | `_parse_cli_args` → `args.func(args)` | 常规路径：`argparse` 建完整子命令树，`args.func` 派发 |
| 3 | `hermes_cli/main.py` | `def cmd_chat(args)` **L1812** | 关键只有一句：`from cli import main as cli_main` |
| 4 | `cli.py` | 全文 1864 行 | `HermesCLI`，**本身很薄**，肉在 12 个 `hermes_cli/cli_*_mixin.py` |
| 5 | `run_agent.py` | `class AIAgent` **L240**，`__init__` **L262** | ⚠️ `__init__` **只是转发器**，正文一行：`from agent.agent_init import init_agent` |
| 6 | `agent/agent_init.py` | `def init_agent` **L2346**（全文 2503 行） | **真正的装载点**（见第三节） |
| 7 | `agent/conversation_loop.py` | `def run_conversation` **L1655**（全文 1808 行） | **主循环**（见第七节） |

> 一句话链路：
> `hermes_bootstrap` → `hermes_cli/main.py::main` →（fast path 或 argparse）→ `cmd_chat` → `cli.py` → `AIAgent.__init__` → `agent_init.init_agent` → `conversation_loop.run_conversation`

---

## 三、装载点 `agent_init.init_agent`（运行时装配清单）

这是「Hermes 如何加载」的第一条线：**进程里装了什么**。

| 顺序 | 锚点 | 装什么 |
|---|---|---|
| 1 | `_set_defaults` **L511** | 给 agent 铺满默认属性表 |
| 2 | `_finalize_routing` **L425** / `_resolve_api_mode` **L374** | 解析 api_mode、provider、credential_pool |
| 3 | `_init_openai_client` / `_init_anthropic_client` / `_init_bedrock_client` / `_init_moa_client` | 按 provider 建 client（每个 provider 一个 `_init_*`） |
| 4 | `_init_fallback_chain` **L1065** | 模型 fallback 链 |
| 5 | `_load_tools` **L1086** | **工具装载**（见第六节） |
| 6 | `_init_session_state` **L1172** | 会话状态、session_id 发布 |
| 7 | `_init_memory` **L1286** | **记忆装载**（`memory_manager` / `memory_provider`） |
| 8 | `_apply_agent_section` **L1367** | 应用 config 里的 `agent:` 段 |
| 9 | `_init_prompt_cache_config` **L653** | 提示词缓存策略 |
| 10 | `_parse_compression_config` **L1505** | 上下文压缩阈值 |

**要点**：装载完成后，`agent.tools` 是一个 JSON-schema 列表，`agent.valid_tool_names` 是名字集合。**模型是无状态的**——所有「加载」最终要么进 `agent.*` 属性，要么被拼进 system prompt。

---

## 四、系统提示词的三层装载

「Hermes 如何加载」的第二条线：**提示词里写了什么**。核心设计是**按「改动频率」把板块排序**，让隐式前缀缓存尽量命中。

| 文件 | 锚点 | 说明 |
|---|---|---|
| `agent/system_prompt.py` | `build_system_prompt_parts` **L727** | 组装三层，返回 `{"stable", "context", "volatile"}` |
| 同上 | `build_system_prompt` **L790** | 用 `\n\n` 拼三层，缓存到 `agent._cached_system_prompt` |
| 同上 | `invalidate_system_prompt` **L802** | 只在**上下文压缩后**调用，会顺带从磁盘重载记忆、解冻插件段落 |
| 同上 | `reconstruct_static_prefix` **L823** | 恢复（`--resume`）时重建 stable 层，且只有存量 prompt 真的以它开头才用 |

三层内容：

- **stable**：`_identity_parts`（SOUL / IDENTITY）、`_guidance_parts`、编码 brief、`_auto_load_parts`（置顶技能）、进程内常量。→ `agent._cached_system_prompt_static`
- **context**：调用方传入的 `system_message`（**只在 API 调用时注入，永不缓存**）、`_context_files_part`（AGENTS.md / SOUL.md 等项目上下文文件）、工作区快照
- **volatile**：`_skills_prompt`（技能索引）、`_memory_parts`（记忆 / 用户画像 / 外部记忆）、插件段落、`_timestamp_line`、运行时环境提示

**对 OhoPilot 的启发**：`system/context/volatile` 三分不是为了好看，是为了**省钱**。你的 `core/llm.py` 如果直接拼一个大字符串，长会话下缓存命中率会显著低于这个结构。

---

## 五、技能装载（渐进式披露完整链路）

| 步骤 | 文件 / 锚点 | 说明 |
|---|---|---|
| 1 | `agent/prompt_builder.py::build_skills_system_prompt` **L1274** | 入口。只产出 **name + description 索引**，不灌正文 |
| 2 | `_build_skills_manifest` **L1143** | 对每个 `SKILL.md` / `DESCRIPTION.md` 做**文件签名**（mtime+size） |
| 3 | `_load_skills_snapshot` **L1174** | 磁盘快照 `~/.hermes/.skills_prompt_snapshot.json`；manifest 未变就复用，**省掉全量扫盘** |
| 4 | `_parse_skill_file` **L1227** | 读 front matter，过 `skill_matches_platform` / `_environment` / `_apps` 三道门 |
| 5 | `_skill_should_show` **L1241** | 条件激活：`requires_tools` / `requires_toolsets` 缺则隐藏；`fallback_for_tools` / `fallback_for_toolsets` 有主选则隐藏；`session_platforms` 不匹配则隐藏 |
| 6 | `agent/skill_utils.py::extract_skill_conditions` **L658** | 从 front matter 抽出上述条件 |
| 7 | `agent/system_prompt.py::_skills_prompt` **L300** | 调用点。**没有 skills 工具就返回空**——技能索引是工具集驱动的 |
| 8 | `agent/system_prompt.py::_auto_load_parts` **L316** | `skills.auto_load` 置顶技能，每 agent 生命周期只解析一次，保证 prompt 字节稳定 |
| 9 | 正文按需读取 | 模型看到索引后调 `skill_view` 才加载全文 |

**技能目录优先级**（`agent/skill_utils.py`）：

1. `get_project_skills_dirs()` **L515** —— 项目本地 `<root>/.hermes/skills`、`<root>/.agents/skills`（root = 最近的 `.git` 祖先）。**最高优先级**，但只有 root 在 `skills.trusted_project_dirs` 里才加载（防提示词注入），加载后会**覆盖同名**的 profile / 内置技能
2. `get_skills_dir()` = `~/.hermes/skills/`（`hermes_constants.py:1192`）
3. `get_skill_create_dir()`（新建技能写入位置）
4. `get_external_skills_dirs()`（`skills.external_dirs`，只读，同名冲突时**输给**本地技能）

> 磁盘真实布局：`~/.hermes/skills/<category>/<name>/SKILL.md`。当前机器上有 12 个分类（apple / creative / devops / email / media / note-taking / productivity / research / social-media / software-development / web / autonomous-ai-agents）。

**最值得抄的一点**：用 **文件签名 manifest 做快照失效**。比每次扫盘便宜，又比纯内存缓存可靠（换进程仍在）。

---

## 六、工具装载（registry 自动发现）

依赖链（单向，注释里写明「no deps」）：

```
tools/registry.py  ←  tools/*.py（import 时自己 register）  ←  model_tools.py（发现）  ←  run_agent.py / cli.py
```

| 文件 | 锚点 | 说明 |
|---|---|---|
| `tools/registry.py` | `class ToolRegistry` **L427**、`class ToolEntry` **L182** | 注册表本体。用 AST 扫 `_module_registers_tools` **L69** 判断哪些模块会注册工具 |
| 同上 | `discover_builtin_tools` **L96** | 自动发现，带磁盘缓存（`_discovery_cache_path` **L144**） |
| 同上 | `_check_fn_cached` **L325** | 工具的可用性检查（check_fn）带 TTL 缓存，避免每次都探环境 |
| `agent/agent_init.py` | `_load_tools` **L1086** | 先 `discover_plugins()`，再取快照，并记录 registry 的 `_generation` 以检测并发刷新 |
| `model_tools.py` | `get_tool_definitions` **L213** | 对外主函数，带缓存 key `_tool_defs_cache_key` **L255** |
| 同上 | `_select_tool_names` **L314** | 按 enabled/disabled toolsets 过滤 |
| 同上 | `_apply_dynamic_schemas` **L482** | **动态改写 schema**（如按当前可用工具重写 `execute_code`、`browser_*`、`delegate_task`）——这是它的巧思，不是所有 agent 都有 |
| 同上 | `handle_function_call` **L872** | 工具调用的总入口：前置守卫 → 审批 → 执行 → 后置 hook |
| `toolsets.py` | `TOOLSETS` / `_HERMES_CORE_TOOLS` | 工具集定义表 |

**对 OhoPilot 的启发**：你的 `tools/base.py` 已经统一了 `Tool` 基类，再往前一步就是**「import 即注册 + 目录扫描发现」**，这样加工具不用动任何注册中心代码。

---

## 七、主循环

| 文件 | 锚点 | 说明 |
|---|---|---|
| `agent/conversation_loop.py` | `run_conversation` **L1655** | 会话主循环（全文 1808 行） |
| `agent/turn_facade.py` | `run_conversation` **L22** | mixin 外观，`AIAgent` 继承它 |
| `agent/turn_*.py` | **31 个文件** | 轮次阶段被拆细：`turn_api_request` / `turn_tool_round` / `turn_tool_validation` / `turn_overflow` / `turn_recovery` / `turn_stop_gates` / `turn_compression` … |

**读法**：**不要顺着 facade 往下追**。想看「工具轮次」就 `grep` `turn_tool_round.py`，想看「溢出/压缩」就看 `turn_overflow.py`。

---

## 八、多端：gateway / tui_gateway / desktop 的关系

这是 OhoPilot「单 core 多端」最直接的对标。

| 层 | 文件 | 说明 |
|---|---|---|
| 网关 | `gateway/run.py` → `class GatewayRunner` **L3371**（全文 6196 行） | 组合式：`GatewayStartupMixin` / `GatewayTurnMixin` / `GatewayAgentCacheMixin` / `GatewayShutdownMixin` … 分布在 `run_*.py` |
| 平台适配 | `gateway/platforms/` | 一个平台一个适配器；新增请看 `platforms/ADDING_A_PLATFORM.md` |
| TUI/Desktop 后端 | `tui_gateway/entry.py` + `server.py`（3512 行）+ `methods_*.py` | **JSON-RPC** 传输。`entry.py` 管进程骨架（信号、崩溃日志、MCP 启动） |
| 终端 UI | `ui-tui/` | Ink（React），`hermes --tui` |
| 桌面 | `apps/desktop/`（Electron）+ `apps/shared`（JSON-RPC 客户端） | |
| 编辑器 | `acp_adapter/` | ACP 协议服务 |

**关键结论**：Hermes 的多端**没有各自实现 agent**，而是「一个 GatewayRunner 进程 + 若干薄客户端」。这正是 OhoPilot 已经拍板的 `client-server` 方向（`ohopilot/server/app.py` 的 SSE 就是同一思路）。差别是 Hermes 在 JSON-RPC 之上又套了 IM 平台适配层，你明确说过不抄那部分。

---

## 九、状态持久化

| 文件 | 锚点 | 说明 |
|---|---|---|
| `hermes_state.py` | `class SessionDB` **L451**、`class AsyncSessionDB` **L1671** | SessionDB 门面，**21 个 `hermes_state_*.py` 兄弟** |
| `hermes_state_schema.py`（76 KB） | — | 表结构 |
| `hermes_state_messages.py`（110 KB） | — | 消息读写，全家最大的兄弟 |
| `hermes_state_search.py`（77 KB） | — | 跨会话检索（FTS5） |
| `hermes_state_compression.py` | — | 压缩落库 |
| `hermes_state_wal.py` | — | WAL / 并发 |
| `hermes_state_repair.py`（67 KB） | — | 自愈 |
| 落盘位置 | `~/.hermes/state.db`（+ `-wal` / `-shm`） | |

**对 OhoPilot 的启发**：你现在的 `memory/store.py` 是「只存 user + 最终 assistant」。Hermes 把**中间 tool_calls 全存**，并用一整套 `repair` / `wal` / `search` 兄弟文件兜住复杂度——代价是 21 个文件。**你现在的取舍是合理的**，等到需要「跨会话检索历史」时再引入 `search`，不必提前背这 21 个文件的复杂度。

---

## 十、路径与用户状态（必须遵守的规矩）

- **绝不硬编码 `~/.hermes`**。用 `hermes_constants.get_hermes_home()` **L111**（代码路径）或 `display_hermes_home()` **L575**（给用户看的文本）。仓库注释里写了：硬编码导致过 5 个 profile 相关的 bug。
- 用户状态全在 `~/.hermes/`：`config.yaml`（设置）、`.env`（**只放密钥**）、`logs/`（agent.log / errors.log / gateway.log）、`skills/`、`memories/`、`sessions/`、`state.db`。
- **一个进程可能服务多个 profile**。任何「跑在轮次之外」的代码必须显式绑定 profile 作用域，否则会静默泄漏默认 profile 的配置（这是它的一个 bug 类，有专门 lint：`scripts/check_profile_scope_patterns.py`）。

> 对 OhoPilot：`ohopilot/config.py` 走环境变量 + `.env` 的方向和它一致；如果要支持多工作区/多 profile，**现在就把路径解析收口到一个 `paths.py`**，代价远小于事后补。

---

## 十一、目录地图 & 每个目录的 AGENTS.md

**先读文档再读代码**——这个仓库自带维护者写的架构导读，能省一半时间：

| 要改的地方 | 先读 |
|---|---|
| `run_agent.py`、`agent/` | `agent/AGENTS.md` |
| `cli.py`、`hermes_cli/` | `hermes_cli/AGENTS.md` |
| `gateway/` | `gateway/AGENTS.md` |
| `tools/`、`toolsets.py`、`model_tools.py` | `tools/AGENTS.md` |
| `plugins/` | `plugins/AGENTS.md` |
| `tui_gateway/`、`ui-tui/` | `tui_gateway/AGENTS.md` |
| `skills/` | `skills/AGENTS.md` |
| `cron/`、kanban | `cron/AGENTS.md` |

**长文设计文档**在 `website/docs/developer-guide/`，重点几篇：

- `architecture.md` —— 总览
- `agent-loop.md` —— 主循环
- `prompt-assembly.md` —— 提示词组装（对应本文第四节）
- `context-compression-and-caching.md` —— 压缩与缓存
- `gateway-internals.md` —— 网关内部
- `creating-skills.md` —— 技能创作规范（front matter 全字段）
- `session-storage.md`、`tools-runtime.md`、`cron-internals.md`

根目录 `AGENTS.md` 的 **Routing Table**（约 L508）是这张表的上游来源。

---

## 十二、OhoPilot ↔ Hermes 逐条对照

| OhoPilot 现有 | Hermes 对应 | 建议动作 |
|---|---|---|
| `ohopilot/cli.py`（`oho` / `run` / `serve` / `doctor`） | `hermes_cli/main.py::main` + `cli.py` | 可以保持单文件；**等子命令超过 8 个**再拆 `commands/` |
| `ohopilot/core/agent.py` 主循环 | `agent/conversation_loop.py` + `agent/turn_*.py` | 先单文件跑通；把「轮次阶段」按函数切分即可，不必一上来拆 31 个文件 |
| `ohopilot/core/llm.py`（LiteLLM） | `agent/agent_init.py` 的 `_init_*_client` + `_init_fallback_chain` | **补 fallback 链**：主模型失败自动降级，这是上线刚需 |
| `ohopilot/core/events.py`（统一事件协议） | `gateway/stream_*.py` + `tui_gateway/event_publisher.py` | 方向正确，是你的「薄客户端」前提 |
| `ohopilot/server/app.py`（FastAPI + SSE） | `tui_gateway/server.py`（JSON-RPC） | SSE 更简单，**建议保留 SSE**，不要为对齐而换 JSON-RPC |
| `ohopilot/skills/loader.py`（渐进式披露） | `agent/prompt_builder.py::build_skills_system_prompt` | **重点补**：① 磁盘快照 + 文件签名失效；② front matter 条件激活（`requires_tools` 等）；③ 目录优先级 |
| `ohopilot/tools/base.py` + 各工具 | `tools/registry.py` + `model_tools.py` | 把「手工登记」升级为「import 即注册 + 扫描发现」 |
| `ohopilot/memory/store.py` | `hermes_state.py` + 21 个兄弟 | 当前取舍合理，别提前背复杂度 |
| `ohopilot/config.py` | `hermes_constants.py` + `hermes_cli/config.py` | **把路径解析收口**，为将来多工作区留口 |
| 安全审批（`shell.run` 禁清单） | `tools/approval*.py`（8 个文件） | 你现在的硬禁清单 + 逐条确认已覆盖核心；将来可加「会话级 YOLO / 一次性放行」 |
| —— | `hermes_platform/` | 机器事实（OS / 架构 / WSL / Termux）统一出口，**值得抄**：别散落 `platform.system()` |

**明确不抄**（已拍板）：Hermes 的 15+ IM 网关（`gateway/platforms/` 那套）、Hermes 的 `pm` 包管理器与 self-update 流水线、`plugins/` 的兼容层机制（`COMPAT_MANIFEST.md` / `compat_manifest.json` —— 那是给「已经发布、不能断兼容」的项目用的，你还没这个负担）。

---

## 十三、阅读顺序 checklist

按这个顺序走，每一步都有明确产出：

- [ ] **0.5 天** — 读根 `AGENTS.md` 的 Project Structure + Routing Table，建立地图
- [ ] **0.5 天** — `pyproject.toml` 三条入口 → `hermes_cli/main.py::main` → `cmd_chat` → `cli.py`；跑通「命令怎么变成一个会话」
- [ ] **1 天** — `agent/agent_init.py::init_agent` + `_load_tools` + `tools/registry.py` + `model_tools.py`；产出「工具系统怎么发现和装载」
- [ ] **1 天** — `agent/system_prompt.py` + `agent/prompt_builder.py` + `agent/skill_utils.py`；产出「技能索引与三层 prompt」
- [ ] **2 天** — `agent/conversation_loop.py` + 按需读 `agent/turn_*.py`；产出「主循环的轮次阶段」
- [ ] **按需** — `gateway/run.py`（多端）、`tui_gateway/`（RPC 后端）、`hermes_state.py`（持久化）、`cron/scheduler.py`（定时）

**两个避坑提醒**

1. 别用「读 god file」的方式读。`hermes_cli/` 有 463 个文件，`hermes_state.py` / `gateway/run.py` / `cli.py` 全是门面。用 `grep -rn "def 名字" <目录>/<stem>_*.py` **按主题找兄弟文件**。
2. 别把 `~/.hermes/hermes-agent` 和 `~/hermes-src` 搞混。两者内容完全一致，读一份就行。

---

*笔记基于 commit `0ab397fd` 通读整理；行号仅供参考，定位请以函数名为准。*
