import { useEffect, useState, type AnchorHTMLAttributes, type MouseEvent } from "react";

/**
 * 极简客户端路由：工作台 / 控制台两个视图在同一个 SPA 里切换，不整页刷新。
 *
 * 为什么不用 react-router：全站一共 7 个路径、没有嵌套路由和动态参数需要解析，
 * 一个 pushState + 自定义事件就够。真正需要路由库的是"多层嵌套 + 守卫 + 懒加载编排"，
 * 我们这儿是"两个视图"，引库反而多一层抽象。
 *
 * 为什么必须客户端切换而不是普通 <a href>：整页刷新会打断正在跑的流
 * （SSE 断了、界面上已经流出来的字全没了），还会让工作台重新挂载一次、重拉一遍会话列表。
 */

const EVENT = "ohopilot:navigate";

/** 当前地址。pushState 本身不触发任何原生事件，所以下面要自己广播。 */
function current(): string {
  return window.location.pathname + window.location.search;
}

/** 编程式跳转：改历史记录 + 通知订阅者。 */
export function navigate(to: string): void {
  if (to === current()) return;
  window.history.pushState(null, "", to);
  window.dispatchEvent(new Event(EVENT));
}

/** 订阅当前地址：navigate、浏览器前进/后退、手改地址后点回车，三种都会更新。 */
export function useRoute(): string {
  const [route, setRoute] = useState(current);

  useEffect(() => {
    const sync = () => setRoute(current());
    window.addEventListener("popstate", sync);
    window.addEventListener(EVENT, sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener(EVENT, sync);
    };
  }, []);

  return route;
}

type LinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  /** 目标路径，写法和 href 一样（/console/tools、/?session=xxx） */
  to: string;
};

/**
 * 站点内链接：左键点击走客户端切换，其余情况交给浏览器默认行为。
 *
 * 为什么还渲染成 <a href>：中键点击、⌘/Ctrl 点击、右键"在新标签打开"都是
 * 用户明确的另开一页意图，不该被我们拦掉；也让地址可复制、可收藏。
 */
export function Link({ to, onClick, ...rest }: LinkProps) {
  function handleClick(ev: MouseEvent<HTMLAnchorElement>) {
    onClick?.(ev);
    if (ev.defaultPrevented) return;
    // button !== 0 是鼠标中键/右键；带修饰键的点击按浏览器默认（新标签/新窗口）
    if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    ev.preventDefault();
    navigate(to);
  }

  return <a {...rest} href={to} onClick={handleClick} />;
}
