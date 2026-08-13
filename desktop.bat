@echo off
REM mBuddy 桌面版启动脚本 (Windows)
cd /d "%~dp0"
if not exist ".env" (
    echo [提示] 未找到 .env，请先复制 .env.example 为 .env 并填入 DEEPSEEK_API_KEY
    copy .env.example .env
    notepad .env
)
echo 正在启动 mBuddy 桌面版 ...
uv run python desktop_app.py
pause
