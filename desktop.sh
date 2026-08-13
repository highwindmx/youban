#!/usr/bin/env bash
# mBuddy 桌面版启动脚本 (macOS / Linux)
set -e
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  echo "[提示] 未找到 .env，请先复制 .env.example 为 .env 并填入 DEEPSEEK_API_KEY"
  cp .env.example .env
  ${EDITOR:-vi} .env
fi
echo "正在启动 mBuddy 桌面版 ..."
uv run python desktop_app.py
