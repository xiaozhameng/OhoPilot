import { useEffect, useState } from "react";
import { deleteGrant, fetchConsoleOverview, fetchGrants } from "../../api";
import { Link } from "../../router";
import type { ConsoleOverview, DirectoryGrant } from "../../types";
import { fmtMs } from "./shared";

/**
 * 工具页：耗时排行 + 目录白名单 + 失败样本。
 * 排行回答「哪个工具慢」，样本回答「为什么失败」——两张表配合着看。
 * 白名单回答「agent 现在能不问就写哪里」：静默放行必须可见、可撤销，
 * 否则它就是个没人知道的洞。
 */
export function ToolsPage() {
  const [data, setData] = useState<ConsoleOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [grants, setGrants] = useState<DirectoryGrant[]>([]);
  const [grantsFile, setGrantsFile] = useState("");
  const [grantError, setGrantError] = useState<string | null>(null);

  useEffect(() => {
    fetchConsoleOverview()
      .then(setData)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
    void reloadGrants();
  }, []);

  async function reloadGrants() {
    try {
      const body = await fetchGrants();
      setGrants(body.grants);
      setGrantsFile(body.file);
    } catch (err) {
      setGrantError(err instanceof Error ? err.message : String(err));
    }
  }

  async function revoke(id: string) {
    try {
      await deleteGrant(id);
      await reloadGrants();
    } catch (err) {
      setGrantError(err instanceof Error ? err.message : String(err));
    }
  }

  if (error) return <p className="settings__msg settings__msg--err">加载失败：{error}</p>;
  if (!data) return <p className="settings__note">加载中…</p>;

  return (
    <>
      <header className="page-head">
        <h1>工具调用</h1>
        <p>平均耗时高 + 失败多 = 该加超时或缓存；最大耗时远超平均 = 偶发卡死</p>
      </header>

      <section className="panel">
        <h2 className="panel__title">耗时与失败排行</h2>
        {data.tool_stats.length === 0 ? (
          <p className="settings__note">还没有工具调用。</p>
        ) : (
          <table className="dtable">
            <thead>
              <tr>
                <th>工具</th>
                <th>调用</th>
                <th>失败</th>
                <th>失败率</th>
                <th>平均耗时</th>
                <th>最大耗时</th>
              </tr>
            </thead>
            <tbody>
              {data.tool_stats.map((t) => (
                <tr key={t.name}>
                  <td>
                    <code>{t.name}</code>
                  </td>
                  <td>{t.calls}</td>
                  <td className={t.errors > 0 ? "dtable__bad" : undefined}>{t.errors}</td>
                  <td>{t.calls ? `${Math.round((t.errors / t.calls) * 100)}%` : "—"}</td>
                  <td>{fmtMs(t.avg_ms)}</td>
                  <td>{fmtMs(t.max_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <h2 className="panel__title">目录白名单</h2>
        <p className="settings__note">
          这些目录里的写操作不再逐次问你（删除、搬运类命令仍然要确认）。撤销后当场恢复逐次审批。
        </p>
        {grantError && <p className="settings__msg settings__msg--err">{grantError}</p>}
        {grants.length === 0 ? (
          <p className="settings__note">还没有白名单。审批时选「以后都不用问」才会产生。</p>
        ) : (
          <table className="dtable">
            <thead>
              <tr>
                <th>目录</th>
                <th>适用工具</th>
                <th>范围</th>
                <th>加入时间</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {grants.map((g) => (
                <tr key={g.id}>
                  <td>
                    <code>{g.path}</code>
                  </td>
                  <td>
                    <code>{g.tool}</code>
                  </td>
                  <td>{g.kind === "dir" ? "目录及子目录" : "单个文件"}</td>
                  <td>{g.created_at.replace("T", " ")}</td>
                  <td>
                    <button className="pill" onClick={() => void revoke(g.id)}>
                      撤销
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {grantsFile && <p className="settings__note">落盘位置：{grantsFile}</p>}
      </section>

      <section className="panel">
        <h2 className="panel__title">最近失败样本</h2>
        {data.error_samples.length === 0 ? (
          <p className="settings__note">没有失败记录。</p>
        ) : (
          <ul className="errlist">
            {data.error_samples.map((e, i) => (
              <li key={i} className="errlist__item">
                <div className="errlist__meta">
                  <code>{e.name}</code>
                  <span>{e.created_at.replace("T", " ")}</span>
                  <Link className="pill pill--link" to={`/console/sessions?session=${e.session_id}`}>
                    会话 {e.session_id}
                  </Link>
                </div>
                <pre className="errlist__excerpt">{e.excerpt || "（无错误信息）"}</pre>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
