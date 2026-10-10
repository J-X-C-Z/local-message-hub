@echo off
setlocal
cd /d "%~dp0"
if exist "integrations\wechat\runtime\venv\Scripts\python.exe" (
  "integrations\wechat\runtime\venv\Scripts\python.exe" -m hub.mcp_server --storage "%~dp0.local"
) else (
  python -m hub.mcp_server --storage "%~dp0.local"
)
