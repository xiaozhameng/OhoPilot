#!/usr/bin/env bash
# =============================================================================
# OhoPilot 一键启动脚本
#
#   ./start.sh              起网页版：API + 界面同端口，浏览器自动打开
#   ./start.sh --dev        开发模式：后端 + vite 热更新（改前端不用重新 build）
#   ./start.sh --rebuild    强制重新构建前端界面
#   ./start.sh --no-open    不自动开浏览器（远程/无头环境用）
#   ./start.sh --port 9000  临时换端口（只本次生效，不改 .env）
#
# 为什么要有这个脚本：
#   手动启动要记四件事——用哪个 python、依赖有没有装、前端有没有构建过、
#   端口有没有被占。记错任何一件，表现都是「打不开」而不是明确的报错。
#   脚本把它们固化成「检查 → 构建 → 启动」，缺什么当场说清楚。
#
# 阻塞行为：服务在前台跑。Ctrl+C 或直接关闭终端窗口，服务就停。
#   没有任何后台进程残留（不需要再记 kill / lsof 这类命令）。
# =============================================================================
set -euo pipefail

# 脚本可能被从任意目录调用（比如 ~ 下执行 ~/Desktop/.../start.sh），
# 先把自己挪到脚本所在的目录，后面所有相对路径才有意义。
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
PORT_OVERRIDE=""
DO_BUILD=0
DO_DEV=0
OPEN_BROWSER=1

# ---- 颜色：只在终端里加，重定向到文件时不输出转义符 ----
if [[ -t 1 ]]; then
  B=$'\033[1m'; DIM=$'\033[2m'
  RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; CYN=$'\033[36m'; RST=$'\033[0m'
else
  B=""; DIM=""; RED=""; GRN=""; YEL=""; CYN=""; RST=""
fi

info() { printf '%s\n' "${CYN}▸${RST} $*"; }
ok()   { printf '%s\n' "${GRN}✓${RST} $*"; }
warn() { printf '%s\n' "${YEL}!${RST} $*"; }
die()  { printf '%s\n' "${RED}✗ $*${RST}" >&2; exit 1; }

usage() {
  cat <<'EOF'
用法: ./start.sh [选项]

  （无参数）      起网页版：API 与界面同一端口，自动打开浏览器
  --dev           开发模式：后端 + vite 开发服务器，前端改动即时热更新
  --rebuild       强制重新构建前端界面（改了 web/ 下代码但没跑 build 时用）
  --port <端口>   临时换端口，只本次生效，不改 .env
  --no-open       不自动打开浏览器
  -h, --help      显示本帮助

停止：Ctrl+C，或直接关掉这个终端窗口。
EOF
}

# ---------------------------------------------------------------------------
# 1. 解析参数
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev)      DO_DEV=1; shift ;;
    --rebuild)  DO_BUILD=1; shift ;;
    --no-open)  OPEN_BROWSER=0; shift ;;
    --port)
      [[ $# -ge 2 ]] || die "--port 后面要跟端口号，例如 ./start.sh --port 9000"
      PORT_OVERRIDE="$2"; shift 2 ;;
    -h|--help)  usage; exit 0 ;;
    *)          warn "不认识的参数：$1"; usage; exit 1 ;;
  esac
done

if [[ -n "$PORT_OVERRIDE" && ! "$PORT_OVERRIDE" =~ ^[0-9]+$ ]]; then
  die "--port 后面必须是数字，收到的是：$PORT_OVERRIDE"
fi

# ---------------------------------------------------------------------------
# 2. 环境检查（缺什么就说清怎么补，不要让它跑到一半崩）
# ---------------------------------------------------------------------------
[[ -x "$PY" ]] || die "找不到虚拟环境解释器：$PY
   首次使用请先建环境：
     python3 -m venv .venv
     .venv/bin/pip install -e '.[server,dev]'"

if ! "$PY" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  die "缺少服务端依赖（fastapi / uvicorn）。补装：
     .venv/bin/pip install -e '.[server]'"
fi

if [[ ! -f .env ]]; then
  warn "没有 .env，已从 .env.example 复制一份。请填入 OHOPILOT_API_KEY 后重新启动："
  printf '     %s\n' "${DIM}open -e .env${RST}"
  cp .env.example .env
fi

# 一次性读出真实配置（走 Config.load()，所以 .env 与环境变量都会生效）
IFS=$'\t' read -r HOST PORT MODEL WORKSPACE HASKEY <<<"$("$PY" - <<'PY'
from ohopilot.config import Config
from ohopilot.core.llm import has_credentials

cfg = Config.load()
print(cfg.host, cfg.port, cfg.model, cfg.workspace, 1 if has_credentials(cfg) else 0, sep="\t")
PY
)"

if [[ -n "$PORT_OVERRIDE" ]]; then
  PORT="$PORT_OVERRIDE"
fi

if [[ "$HASKEY" != "1" ]]; then
  warn "没有检测到模型密钥（OHOPILOT_API_KEY 为空）——网址能打开，但一问就报错。"
fi

# ---------------------------------------------------------------------------
# 3. 端口占用检查（不然 uvicorn 只报一句 address already in use，很难懂）
# ---------------------------------------------------------------------------
port_is_free() {
  "$PY" - "$HOST" "$PORT" <<'PY'
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind((host, port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
}

if ! port_is_free; then
  die "端口 $PORT 已被占用。可能是已经有一个 OhoPilot 在跑。
   先打开看看：http://$HOST:$PORT
   查是谁占的：lsof -nP -iTCP:$PORT -sTCP:LISTEN
   或者换个端口：./start.sh --port 9000"
fi

# ---------------------------------------------------------------------------
# 4. 前端构建（只在网页模式下需要；开发模式跑 vite，不用构建产物）
# ---------------------------------------------------------------------------
if [[ "$DO_DEV" == "0" ]]; then
  need_build=0
  [[ "$DO_BUILD" == "1" ]] && need_build=1
  [[ -f ohopilot/server/static/index.html ]] || need_build=1

  if [[ "$need_build" == "1" ]]; then
    if command -v npm >/dev/null 2>&1; then
      if [[ ! -d web/node_modules ]]; then
        info "首次构建，先安装前端依赖（约 200MB，可能需要几十秒）"
        ( cd web && npm install --no-audit --no-fund )
      fi
      info "构建前端界面（npm run build）"
      ( cd web && npm run build ) >/dev/null || die "前端构建失败。手动跑一次看完整报错：
     cd web && npm run build"
      ok "界面已构建"
    else
      warn "没有找到 npm，跳过界面构建：只能用 API（/health 等），没有网页界面。
   想用网页版请先装 Node.js 18+，然后执行：cd web && npm install && npm run build"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 5a. 开发模式：后端 + vite 两个进程，Ctrl+C 一起收掉
# ---------------------------------------------------------------------------
open_url() {
  if command -v open >/dev/null 2>&1; then          # macOS
    open "$1"
  elif command -v xdg-open >/dev/null 2>&1; then    # Linux
    xdg-open "$1" >/dev/null 2>&1 &
  fi
}

# 等端口真的能响应了再开浏览器，避免开出一个「无法访问」的空白页
open_when_ready() {
  local url="$1"
  (
    for _ in $(seq 1 60); do
      if curl -s --noproxy '*' -o /dev/null --max-time 1 "${url}/health"; then
        break
      fi
      sleep 0.5
    done
    open_url "$url"
  ) >/dev/null 2>&1 &
}

if [[ "$DO_DEV" == "1" ]]; then
  command -v npm >/dev/null 2>&1 || die "开发模式需要 npm（Node.js 18+），当前 PATH 里没有找到"
  if [[ ! -d web/node_modules ]]; then
    info "安装前端依赖（首次约 200MB）"
    ( cd web && npm install --no-audit --no-fund )
  fi

  printf '\n%sOhoPilot 开发模式%s\n' "$B" "$RST"
  printf '  %-6s %s\n' "后端" "http://$HOST:$PORT"
  printf '  %-6s %s\n' "界面" "http://127.0.0.1:5173  ${DIM}(vite 热更新，改 web/src 即时生效)${RST}"
  printf '  %-6s %s\n' "模型" "$MODEL"
  printf '  %-6s %s\n\n' "目录" "$WORKSPACE"

  OHOPILOT_PORT="$PORT" "$PY" -m ohopilot.server &
  BACKEND_PID=$!

  ( cd web && OHOPILOT_API="http://127.0.0.1:$PORT" npm run dev ) &
  VITE_PID=$!

  cleanup() {
    trap - INT TERM EXIT
    kill "$VITE_PID" "$BACKEND_PID" 2>/dev/null || true
    wait 2>/dev/null || true
    printf '\n%s已停止（后端与 vite 都已退出）%s\n' "$DIM" "$RST"
  }
  trap cleanup INT TERM EXIT

  info "按 ${B}Ctrl+C${RST} 停止（会同时收掉后端和 vite）"
  echo
  [[ "$OPEN_BROWSER" == "1" ]] && open_url "http://127.0.0.1:5173"
  wait
  exit 0
fi

# ---------------------------------------------------------------------------
# 5b. 默认模式：只起一个进程，前台阻塞
# ---------------------------------------------------------------------------
printf '\n%sOhoPilot 已启动%s\n' "$B$GRN" "$RST"
printf '  %-6s %s\n' "界面" "http://$HOST:$PORT"
printf '  %-6s %s\n' "模型" "$MODEL"
printf '  %-6s %s\n' "目录" "$WORKSPACE"
printf '  %-6s %s\n\n' "密钥" "$([[ "$HASKEY" == "1" ]] && echo '已配置' || echo '缺失，问答会失败')"
info "按 ${B}Ctrl+C${RST} 停止；直接关掉这个终端窗口也会停止服务"
echo

[[ "$OPEN_BROWSER" == "1" ]] && open_when_ready "http://$HOST:$PORT"

# 关掉 set -e：Ctrl+C 时 python 会以 130 退出，那是正常停止，不该当异常
set +e
OHOPILOT_PORT="$PORT" "$PY" -m ohopilot.server
CODE=$?
set -e

if [[ "$CODE" -eq 0 || "$CODE" -eq 130 ]]; then
  printf '\n%s服务已停止%s\n' "$DIM" "$RST"
else
  die "服务异常退出（退出码 $CODE），往上翻日志看具体报错"
fi
