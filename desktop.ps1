# mBuddy 桌面版启动脚本 (PowerShell)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.env')) {
    Write-Host '[提示] 未找到 .env，请先复制 .env.example 为 .env 并填入 DEEPSEEK_API_KEY'
    Copy-Item .env.example .env
    notepad .env
}
Write-Host '正在启动 mBuddy 桌面版 ...'
uv run python desktop_app.py
