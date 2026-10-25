#!/usr/bin/env bash
# OhoPilot 一键安装（第 28 步）
# 用法：
#   ./install.sh              # 完整安装：venv + 全部依赖组 + .env + 自检
#   ./install.sh --core       # 只装核心（不含 docs/rag 可选组）
#   ./install.sh --with-tests # 额外跑一遍 pytest 自检
#
# 幂等：重复执行安全，已装的东西不会重装。

set -euo pipefail
cd "$(dirname "$0")"

CORE=1
RUN_TESTS=0
for arg in "$@"; do
  case "$arg" in
    --core) CORE=0 ;;
    --with-tests) RUN_TESTS=1 ;;
    *) echo "未知参数：$arg"; exit 1 ;;
  esac
done

say() { printf '\n\033[1;34m== %s\033[0m\n' "$1"; }
ok()  { printf '\033[1;32m✓ %s\033[0m\n' "$1"; }
warn(){ printf '\033[1;33m! %s\033[0m\n' "$1"; }

# ---------- 1. 找 Python（优先 3.13，其次 3.12+） ----------
say "检查 Python"
PY=""
for cand in python3.13 python3.12 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
      PY="$cand"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "需要 Python >= 3.12（推荐 3.13）。请先安装：https://www.python.org/downloads/"; exit 1
fi
ok "使用 $PY ($($PY --version 2>&1))"

# ---------- 2. 建 venv ----------
say "准备虚拟环境 .venv"
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
  ok "已创建 .venv"
else
  ok "已有 .venv，跳过创建"
fi
PIP=".venv/bin/pip"

# ---------- 3. 装依赖（失败自动换清华镜像重试一次） ----------
say "安装依赖"
EXTRAS="dev,server"
if [ "$CORE" -eq 1 ]; then
  EXTRAS="dev,server,docs,rag"
fi
if ! "$PIP" install -q -e ".[$EXTRAS]" 2>/dev/null; then
  warn "默认源失败，换清华镜像重试"
  "$PIP" install -q -e ".[$EXTRAS]" -i https://pypi.tuna.tsinghua.edu.cn/simple
fi
ok "依赖就绪（[$EXTRAS]）"

# ---------- 4. 生成 .env ----------
say "准备 .env"
if [ -f .env ]; then
  ok "已有 .env，不动它（密钥在里面）"
else
  cp .env.example .env
  warn "已从 .env.example 生成 .env——请打开它填 OHOPILOT_API_KEY，否则 agent 无法调用模型"
fi

# ---------- 5. 前端产物（有 Node 才做；没有也能用，只是没有网页界面） ----------
say "检查前端构建产物"
if [ -f ohopilot/server/static/index.html ]; then
  ok "已存在，跳过"
elif command -v npm >/dev/null 2>&1; then
  (cd web && npm install && npm run build)
  ok "前端已构建"
else
  warn "没有 npm：网页界面暂不可用（CLI 不受影响）。装 Node 22 后在 web/ 里跑 npm install && npm run build"
fi

# ---------- 6. 自检 ----------
say "自检"
.venv/bin/python - << 'PYEOF'
from ohopilot.config import Config
from ohopilot.runtime import Runtime
rt = Runtime.create()
print(f"  工具 {len(rt.registry.names())} 个：{', '.join(rt.registry.names()[:6])} …")
rt.close()
PYEOF
ok "内核可正常装配"

if [ "$RUN_TESTS" -eq 1 ]; then
  .venv/bin/python -m pytest -q --basetemp=/tmp/oho-pytest
  ok "测试通过"
fi

say "完成"
cat << TIP

下一步：
  1. 编辑 .env，确认 OHOPILOT_API_KEY 已填
  2. ./start.sh          # 起服务并打开网页工作台
  3. .venv/bin/oho       # 或用终端版

TIP
