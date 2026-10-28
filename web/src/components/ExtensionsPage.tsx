import { useEffect, useState } from "react";
import {
  addMcpServer,
  deleteMcpServer,
  deleteSkill,
  discoverSkills,
  fetchMcp,
  fetchSkills,
  installSkill,
} from "../api";
import type {
  AuditFinding,
  McpServer,
  SkillCandidate,
  SkillInfo,
  SkillInstallResult,
} from "../types";

/**
 * 扩展中心：agent 使用者配置「agent 能干什么」的页面。
 * 和控制台（开发者看数据）分开：这里改的是能力配置——装技能、挂 MCP 服务器。
 * 两块能力共用一个页面（都是「给 agent 加本事」），页内用标签切换。
 */

/** 常用 MCP 模板：点一下把配置带进表单，改改参数就能加。uvx/npx 要求本机装了 uv/node */
const MCP_PRESETS: Array<{ label: string; name: string; command: string; args: string[] }> = [
  { label: "fetch（网页抓取）", name: "fetch", command: "uvx", args: ["mcp-server-fetch"] },
  {
    label: "filesystem（文件访问）",
    name: "filesystem",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-filesystem", "~/Desktop"],
  },
  {
    label: "memory（长期记忆）",
    name: "memory",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-memory"],
  },
];

export function ExtensionsPage() {
  // 技能标签页的状态
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [skillsDir, setSkillsDir] = useState("");
  const [source, setSource] = useState("");
  const [repo, setRepo] = useState("");
  const [candidates, setCandidates] = useState<SkillCandidate[] | null>(null);
  const [installed, setInstalled] = useState<SkillInstallResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 技能名 → 布尔。谁正在安装/删除，按钮转圈防重复点 */
  const [pendingSkill, setPendingSkill] = useState<string | null>(null);

  // MCP 标签页的状态
  const [mcpPath, setMcpPath] = useState("");
  const [servers, setServers] = useState<Record<string, McpServer>>({});
  const [mcpName, setMcpName] = useState("");
  const [mcpCommand, setMcpCommand] = useState("");
  const [mcpArgs, setMcpArgs] = useState("");
  const [mcpEnv, setMcpEnv] = useState("");
  const [jsonPaste, setJsonPaste] = useState("");
  const [mcpNote, setMcpNote] = useState<string | null>(null);
  const [mcpError, setMcpError] = useState<string | null>(null);

  const [tab, setTab] = useState<"skills" | "mcp">("skills");

  async function refreshSkills() {
    const body = await fetchSkills();
    setSkills(body.skills);
    setSkillsDir(body.dir);
  }

  async function refreshMcp() {
    const body = await fetchMcp();
    setMcpPath(body.path);
    setServers(body.servers);
  }

  useEffect(() => {
    refreshSkills().catch((err: unknown) =>
      setError(err instanceof Error ? err.message : String(err)),
    );
    refreshMcp().catch((err: unknown) =>
      setMcpError(err instanceof Error ? err.message : String(err)),
    );
  }, []);

  async function handleDiscover() {
    setBusy(true);
    setError(null);
    setCandidates(null);
    try {
      setCandidates(await discoverSkills(repo));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleInstall(target: string) {
    setBusy(true);
    setError(null);
    try {
      const result = await installSkill({ source: target });
      setInstalled(result);
      if (result.ok) await refreshSkills();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  /** warning 被拦后用户点「仍要安装」：带 confirm_risky 重试同一条 source */
  async function handleForceInstall() {
    setBusy(true);
    try {
      const result = await installSkill({ source, confirm_risky: true });
      setInstalled(result);
      if (result.ok) await refreshSkills();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(name: string) {
    if (!window.confirm(`删除技能 ${name}？`)) return;
    setPendingSkill(name);
    try {
      await deleteSkill(name);
      await refreshSkills();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPendingSkill(null);
    }
  }

  async function handleAddServer() {
    setMcpError(null);
    setMcpNote(null);
    // args 一行一个或空格分隔都行；env 每行 KEY=VALUE
    const args = mcpArgs.trim() ? mcpArgs.trim().split(/\s+/) : [];
    const env: Record<string, string> = {};
    for (const line of mcpEnv.split("\n")) {
      const t = line.trim();
      if (!t) continue;
      const eq = t.indexOf("=");
      if (eq <= 0) {
        setMcpError(`环境变量行不合法（要 KEY=VALUE）：${t}`);
        return;
      }
      env[t.slice(0, eq)] = t.slice(eq + 1);
    }
    try {
      const res = await addMcpServer({ name: mcpName, command: mcpCommand, args, env });
      setMcpNote(res.note);
      setMcpName("");
      setMcpCommand("");
      setMcpArgs("");
      setMcpEnv("");
      setJsonPaste("");
      await refreshMcp();
    } catch (err: unknown) {
      setMcpError(err instanceof Error ? err.message : String(err));
    }
  }

  /** 整段 JSON 导入：接受 Claude Desktop 格式 {"mcpServers": {...}}，也接受裸 {名字: 配置} */
  async function handleImportJson() {
    setMcpError(null);
    setMcpNote(null);
    try {
      const parsed = JSON.parse(jsonPaste) as Record<string, unknown>;
      const map = (parsed.mcpServers ?? parsed) as Record<string, McpServer>;
      const names = Object.keys(map);
      if (names.length === 0) {
        setMcpError("JSON 里没有可导入的服务器");
        return;
      }
      for (const name of names) {
        const spec = map[name];
        await addMcpServer({
          name,
          command: spec.command,
          args: spec.args ?? [],
          env: spec.env ?? {},
        });
      }
      setMcpNote(`已导入 ${names.length} 个服务器，重启 oho-server 后生效`);
      setJsonPaste("");
      await refreshMcp();
    } catch (err: unknown) {
      setMcpError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleDeleteServer(name: string) {
    if (!window.confirm(`移除 MCP 服务器 ${name}？（重启后不再连接）`)) return;
    setMcpError(null);
    try {
      await deleteMcpServer(name);
      await refreshMcp();
    } catch (err: unknown) {
      setMcpError(err instanceof Error ? err.message : String(err));
    }
  }

  const installedNames = new Set(skills.map((s) => s.name));

  return (
    <main className="console ext">
      <header className="console__head">
        <div>
          <h1 className="console__title">扩展中心</h1>
          <p className="console__sub">给 agent 加本事：技能（提示词能力）与 MCP（外部工具）</p>
        </div>
        <a className="pill pill--link" href="/">
          ← 返回工作台
        </a>
      </header>

      <div className="ext__tabs">
        <button
          className={`ext__tab${tab === "skills" ? " ext__tab--on" : ""}`}
          onClick={() => setTab("skills")}
        >
          技能中心（{skills.length}）
        </button>
        <button
          className={`ext__tab${tab === "mcp" ? " ext__tab--on" : ""}`}
          onClick={() => setTab("mcp")}
        >
          MCP 服务器（{Object.keys(servers).length}）
        </button>
      </div>

      {tab === "skills" && (
        <>
          <section className="console__section">
            <h2>已安装的技能</h2>
            <p className="settings__note">目录：{skillsDir}</p>
            {skills.length === 0 ? (
              <p className="settings__note">还没有安装技能。用下面的表单或从仓库挑选。</p>
            ) : (
              <table className="usage__table">
                <thead>
                  <tr>
                    <th>名称</th>
                    <th>触发条件（description）</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {skills.map((s) => (
                    <tr key={s.name}>
                      <td>
                        <code>{s.name}</code>
                      </td>
                      <td>{s.description}</td>
                      <td>
                        <button
                          className="pill"
                          disabled={pendingSkill === s.name}
                          onClick={() => void handleDelete(s.name)}
                        >
                          删除
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="console__section">
            <h2>安装技能</h2>
            <p className="settings__note">
              来源可以是技能文件的 http(s) 直链，或本机路径
              （Claude 形态给目录，会自动找里面的 SKILL.md）。
              安装前会跑安全审计：危险内容直接拒装，可疑内容要你确认。
            </p>
            <div className="ext__row">
              <input
                className="ext__input"
                placeholder="https://example.com/skill.md 或 /path/to/skill-dir"
                value={source}
                onChange={(e) => setSource(e.target.value)}
              />
              <button className="pill pill--btn" disabled={busy || !source.trim()} onClick={() => void handleInstall(source)}>
                安装
              </button>
            </div>
            {installed && <AuditReport result={installed} onForce={() => void handleForceInstall()} busy={busy} />}
          </section>

          <section className="console__section">
            <h2>从 GitHub 仓库挑选</h2>
            <p className="settings__note">
              输入 owner/repo（如 anthropics/skills），列出仓库里的技能文件，逐个安装。
              只在安装时才下载内容，下载后照走安全审计。
            </p>
            <div className="ext__row">
              <input
                className="ext__input"
                placeholder="owner/repo 或 https://github.com/owner/repo"
                value={repo}
                onChange={(e) => setRepo(e.target.value)}
              />
              <button className="pill pill--btn" disabled={busy || !repo.trim()} onClick={() => void handleDiscover()}>
                发现技能
              </button>
            </div>
            {candidates && candidates.length === 0 && (
              <p className="settings__note">这个仓库里没找到长得像技能的 .md 文件。</p>
            )}
            {candidates && candidates.length > 0 && (
              <table className="usage__table">
                <thead>
                  <tr>
                    <th>技能</th>
                    <th>仓库路径</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {candidates.map((c) => (
                    <tr key={c.path}>
                      <td>
                        <code>{c.name}</code>
                      </td>
                      <td className="ext__path">{c.path}</td>
                      <td>
                        <button
                          className="pill pill--btn"
                          disabled={busy}
                          onClick={() => {
                            setSource(c.raw_url);
                            void handleInstall(c.raw_url);
                          }}
                        >
                          {installedNames.has(c.name) ? "覆盖安装" : "安装"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          {error && <p className="settings__msg settings__msg--err">{error}</p>}
        </>
      )}

      {tab === "mcp" && (
        <>
          <section className="console__section">
            <h2>已配置的 MCP 服务器</h2>
            <p className="settings__note">
              配置文件：{mcpPath} · 修改后要重启 oho-server 才生效（子进程是启动时拉起的）
            </p>
            {Object.keys(servers).length === 0 ? (
              <p className="settings__note">还没有配置。用表单、模板或粘贴 JSON。</p>
            ) : (
              <table className="usage__table">
                <thead>
                  <tr>
                    <th>名称</th>
                    <th>启动命令</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(servers).map(([name, spec]) => (
                    <tr key={name}>
                      <td>
                        <code>{name}</code>
                      </td>
                      <td className="ext__path">
                        <code>
                          {spec.command} {(spec.args ?? []).join(" ")}
                        </code>
                        {spec.env && Object.keys(spec.env).length > 0 && (
                          <span className="settings__note">
                            {" "}
                            env：{Object.keys(spec.env).join("、")}
                          </span>
                        )}
                      </td>
                      <td>
                        <button className="pill" onClick={() => void handleDeleteServer(name)}>
                          移除
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="console__section">
            <h2>添加服务器</h2>
            <div className="ext__row">
              {MCP_PRESETS.map((p) => (
                <button
                  key={p.name}
                  className="pill"
                  onClick={() => {
                    setMcpName(p.name);
                    setMcpCommand(p.command);
                    setMcpArgs(p.args.join(" "));
                  }}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <div className="ext__form">
              <label className="ext__field">
                <span>名称（英文标识符）</span>
                <input className="ext__input" value={mcpName} onChange={(e) => setMcpName(e.target.value)} placeholder="fetch" />
              </label>
              <label className="ext__field">
                <span>命令</span>
                <input className="ext__input" value={mcpCommand} onChange={(e) => setMcpCommand(e.target.value)} placeholder="uvx / npx / 可执行文件路径" />
              </label>
              <label className="ext__field">
                <span>参数（空格分隔）</span>
                <input className="ext__input" value={mcpArgs} onChange={(e) => setMcpArgs(e.target.value)} placeholder="mcp-server-fetch" />
              </label>
              <label className="ext__field">
                <span>环境变量（每行 KEY=VALUE，可空）</span>
                <textarea
                  className="ext__input ext__input--area"
                  rows={2}
                  value={mcpEnv}
                  onChange={(e) => setMcpEnv(e.target.value)}
                  placeholder={"API_KEY=sk-xxx\nBASE_URL=https://..."}
                />
              </label>
              <button
                className="pill pill--btn"
                disabled={!mcpName.trim() || !mcpCommand.trim()}
                onClick={() => void handleAddServer()}
              >
                添加
              </button>
            </div>
          </section>

          <section className="console__section">
            <h2>粘贴 JSON 导入</h2>
            <p className="settings__note">
              直接贴别人给的 Claude Desktop 配置（带 mcpServers 包装或裸对象都行），批量导入。
            </p>
            <textarea
              className="ext__input ext__input--area"
              rows={6}
              value={jsonPaste}
              onChange={(e) => setJsonPaste(e.target.value)}
              placeholder={'{\n  "mcpServers": {\n    "fetch": { "command": "uvx", "args": ["mcp-server-fetch"] }\n  }\n}'}
            />
            <div className="ext__row">
              <button className="pill pill--btn" disabled={!jsonPaste.trim()} onClick={() => void handleImportJson()}>
                导入
              </button>
            </div>
          </section>

          {mcpNote && <p className="settings__msg">{mcpNote}</p>}
          {mcpError && <p className="settings__msg settings__msg--err">{mcpError}</p>}
        </>
      )}
    </main>
  );
}

/** 审计报告块：ok=false 且有 warning 时给「仍要安装」；有 danger 只能放弃。 */
function AuditReport({
  result,
  onForce,
  busy,
}: {
  result: SkillInstallResult;
  onForce: () => void;
  busy: boolean;
}) {
  const danger = result.findings.filter((f) => f.severity === "danger");
  const warning = result.findings.filter((f) => f.severity === "warning");
  const blocked = !result.ok && danger.length > 0;
  const needsConfirm = !result.ok && danger.length === 0 && warning.length > 0;

  return (
    <div className={`audit${result.ok ? " audit--ok" : " audit--bad"}`}>
      <p className="audit__msg">{result.message}</p>
      {result.findings.length > 0 && (
        <ul className="audit__list">
          {result.findings.map((f: AuditFinding, i) => (
            <li key={i} className={`audit__item audit__item--${f.severity}`}>
              [{f.severity === "danger" ? "危险" : "注意"}] {f.label}（第 {f.line_no} 行）：
              <code>{f.excerpt}</code>
            </li>
          ))}
        </ul>
      )}
      {needsConfirm && (
        <button className="pill pill--btn" disabled={busy} onClick={onForce}>
          我已了解上述内容，仍要安装
        </button>
      )}
      {blocked && <p className="settings__note">有危险内容，本项目不允许强行安装。</p>}
    </div>
  );
}
