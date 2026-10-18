$ErrorActionPreference = 'Stop'

& (Join-Path $PSScriptRoot 'test-package-privacy.ps1')
& (Join-Path $PSScriptRoot 'test-git-privacy.ps1')
& (Join-Path $PSScriptRoot 'assert-package-privacy.ps1') -Root (git rev-parse --show-toplevel) -GitVisible
& (Join-Path $PSScriptRoot 'test-package-manifest.ps1')
& (Join-Path $PSScriptRoot 'test-package-smoke.ps1')

$python = if ($env:LMH_PYTHON) { $env:LMH_PYTHON } else { 'python' }
& $python -m unittest hub.test_imports hub.test_mcp hub.test_sending hub.test_auto_export hub.test_export_memory hub.test_analytics hub.test_resources hub.test_backups hub.test_account_safety hub.test_voice_transcription hub.test_moments_exports hub.test_startup hub.test_capabilities hub.test_store hub.test_server `
    integrations.wechat.test_cursor integrations.wechat.test_media integrations.wechat.test_moments integrations.wechat.test_provider integrations.wechat.test_sns_media integrations.wecom.test_adapter integrations.wecom.test_config integrations.wecom.test_bot_credentials integrations.wecom.test_bot_media integrations.wecom.test_bot_sender `
    integrations.qq.test_provider integrations.qq.test_sender integrations.qq.test_local_provider integrations.qq.test_local_wal integrations.qq.test_key_reader integrations.qq.test_send_target integrations.telegram.test_adapter integrations.wechat.test_sender hub.test_windows_notifications hub.test_streaming_json -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

node --check webui/app.js
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
node --experimental-strip-types --test scripts/test-source-mutation-gate.cjs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
node --test webui/test-account-activity.cjs webui/test-avatar-cache-render.cjs webui/test-conversation-roster-render.cjs webui/test-conversation-roster-stale-response.cjs webui/test-export-ui-contract.cjs webui/test-group-member-expansion.cjs webui/test-media-gallery-preview.cjs webui/test-media-open-chat.cjs webui/test-memory-bounds.cjs webui/test-memory-browser.cjs webui/test-moments-render.cjs webui/test-notification-click-target.cjs webui/test-notification-support.cjs webui/test-pair-report-export.cjs webui/test-qq-forward-attachments.cjs webui/test-qq-key-discovery.cjs webui/test-qq-local-source.cjs webui/test-qq-rich-content.cjs webui/test-qq-targeted-sync.cjs webui/test-qq-wma-preview.cjs webui/test-send-compose-local-uid.cjs webui/test-send-history-render.cjs webui/test-source-avatar-status-render.cjs webui/test-voice-transcription-render.cjs webui/test-wechat-cached-emoji-render.cjs webui/test-wechat-location-render.cjs webui/test-wecom-bot-media-compose.cjs webui/test-wecom-resource-render.cjs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
node --test webui/test-self-sent-daily-render.cjs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
node --test webui/test-lock-interaction.cjs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
node --test webui/test-refresh-performance.cjs webui/test-navigation-init.cjs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
node --check webui/lock.js
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python -m py_compile hub/imports.py hub/mcp_server.py hub/sending.py hub/auto_export.py hub/analytics.py hub/resources.py hub/backups.py hub/account_safety.py hub/voice_transcription.py hub/moments_exports.py hub/capabilities.py hub/store.py hub/server.py `
    integrations/wechat/provider.py integrations/wechat/test_provider.py integrations/wechat/moments.py integrations/wechat/sns_media.py integrations/wechat/vendor/db.py `
    integrations/wecom/config.py integrations/wecom/bot_credentials.py integrations/wecom/bot_media.py integrations/wecom/bot_sender.py integrations/qq/provider.py integrations/qq/sender.py integrations/qq/key_reader.py integrations/qq/send_target.py integrations/telegram/adapter.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python scripts/benchmark-local-hub.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

git diff --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
