$ErrorActionPreference = 'Stop'

$python = if ($env:LMH_PYTHON) { $env:LMH_PYTHON } else { 'python' }
& $python -m unittest hub.test_imports hub.test_mcp hub.test_store hub.test_server `
    integrations.wechat.test_cursor integrations.wechat.test_media integrations.wecom.test_config `
    integrations.qq.test_provider integrations.telegram.test_adapter -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

node --check webui/app.js
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python -m py_compile hub/imports.py hub/mcp_server.py hub/store.py hub/server.py `
    integrations/wechat/provider.py integrations/wechat/vendor/db.py `
    integrations/wecom/config.py integrations/qq/provider.py integrations/telegram/adapter.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python scripts/benchmark-local-hub.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

git diff --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
