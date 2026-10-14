const assert = require('node:assert/strict')
const path = require('node:path')
const { test } = require('node:test')

const sourcePath = path.join(__dirname, '../electron/services/sourceMutationGate.ts')
const { denySourceMutationRoute, registerSourceMutationHandlers } = require(sourcePath)

test('Electron source-client mutation IPC refuses install and uninstall without calling providers', async () => {
  const handlers = new Map()
  const calls = []
  const operation = (name) => (...args) => calls.push([name, ...args])
  registerSourceMutationHandlers({
    handle: (channel, listener) => handlers.set(channel, listener)
  }, {
    updateMessage: operation('updateMessage'),
    insertTextMessage: operation('insertTextMessage'),
    deleteMessage: operation('deleteMessage'),
    installAntiRevoke: operation('installAntiRevoke'),
    uninstallAntiRevoke: operation('uninstallAntiRevoke'),
    installMomentsBlock: operation('installMomentsBlock'),
    uninstallMomentsBlock: operation('uninstallMomentsBlock'),
    deleteMomentsPost: operation('deleteMomentsPost')
  })

  for (const channel of [
    'chat:updateMessage', 'chat:insertTextMessage', 'chat:deleteMessage',
    'chat:installAntiRevokeTriggers', 'chat:uninstallAntiRevokeTriggers',
    'sns:installBlockDeleteTrigger', 'sns:uninstallBlockDeleteTrigger', 'sns:deleteSnsPost'
  ]) {
    const result = await handlers.get(channel)({}, ['synthetic-session'])
    assert.equal(result.success, false)
    assert.match(result.error, /源客户端数据库修改当前已禁用/)
  }
  assert.deepEqual(calls, [])
})

test('Electron no longer registers source-message write handlers outside the mutation gate', () => {
  const fs = require('node:fs')
  const main = fs.readFileSync(path.join(__dirname, '../electron/main.ts'), 'utf8')
  const gate = fs.readFileSync(path.join(__dirname, '../electron/services/sourceMutationGate.ts'), 'utf8')
  assert.doesNotMatch(main, /ipcMain\.handle\('chat:(?:updateMessage|insertTextMessage|deleteMessage)'/)
  assert.match(main, /registerSourceMutationHandlers\(ipcMain,\s*\{/)
  for (const channel of ['chat:updateMessage', 'chat:insertTextMessage', 'chat:deleteMessage']) {
    assert.ok(gate.includes(`ipcMain.handle('${channel}', deniedHandler(`))
  }
})

test('HTTP SNS block-delete install, uninstall, and post deletion return 403 without calling providers', () => {
  const calls = []
  const responses = []
  for (const [name, operation] of [
    ['install', () => calls.push('install')],
    ['uninstall', () => calls.push('uninstall')],
    ['delete', () => calls.push('delete')]
  ]) {
    denySourceMutationRoute(operation, (status, message) => {
      responses.push({ name, status, body: { error: message } })
    })
  }

  assert.deepEqual(responses.map(({ name, status }) => ({ name, status })), [
    { name: 'install', status: 403 },
    { name: 'uninstall', status: 403 },
    { name: 'delete', status: 403 }
  ])
  assert.match(responses[0].body.error, /源客户端数据库修改当前已禁用/)
  assert.match(responses[1].body.error, /源客户端数据库修改当前已禁用/)
  assert.match(responses[2].body.error, /源客户端数据库修改当前已禁用/)
  assert.deepEqual(calls, [])
})

test('Moments source-mutation HTTP routes remain connected to the fail-closed gate', () => {
  const fs = require('node:fs')
  const main = fs.readFileSync(path.join(__dirname, '../electron/main.ts'), 'utf8')
  const http = fs.readFileSync(path.join(__dirname, '../electron/services/httpService.ts'), 'utf8')
  const ui = fs.readFileSync(path.join(__dirname, '../src/components/Sns/SnsPostItem.tsx'), 'utf8')

  for (const [pathname, method, handler] of [
    ['/api/v1/sns/block-delete/install', 'POST', 'handleSnsBlockDeleteInstall'],
    ['/api/v1/sns/block-delete/uninstall', 'POST', 'handleSnsBlockDeleteUninstall'],
    ['/api/v1/sns/post/', 'DELETE', 'handleSnsDeletePost']
  ]) {
    const route = pathname.endsWith('/')
      ? `pathname.startsWith('${pathname}')`
      : `pathname === '${pathname}'`
    const routePattern = route.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    assert.match(http, new RegExp(`${routePattern}[\\s\\S]*?req\\.method !== '${method}'[\\s\\S]*?await this\\.${handler}\\(`))

    const handlerBody = http.match(new RegExp(`private async ${handler}\\([\\s\\S]*?\\n  }`))?.[0]
    assert.ok(handlerBody, `${handler} must remain defined`)
    assert.match(handlerBody, /denySourceMutationRoute\(/, `${handler} must retain the fail-closed gate`)
    assert.match(handlerBody, /\(\)\s*=>\s*snsService\./, `${handler} must keep provider calls inside the denied thunk`)
  }

  assert.match(main, /deleteMomentsPost:\s*\(postId: string\) => snsService\.deleteSnsPost\(postId\)/)
  assert.doesNotMatch(main, /ipcMain\.handle\('sns:deleteSnsPost'/)
  assert.match(ui, /className="icon-btn-ghost debug-btn delete-btn"[\s\S]*?disabled[\s\S]*?源客户端修改尚未开放/)
})
