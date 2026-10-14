type MutationOperation = (...args: any[]) => unknown

type IpcRegistrar = {
  handle: (channel: string, listener: (...args: any[]) => unknown) => void
}

type SourceMutations = {
  updateMessage: MutationOperation
  insertTextMessage: MutationOperation
  deleteMessage: MutationOperation
  installAntiRevoke: MutationOperation
  uninstallAntiRevoke: MutationOperation
  installMomentsBlock: MutationOperation
  uninstallMomentsBlock: MutationOperation
  deleteMomentsPost: MutationOperation
}

const unavailable = {
  success: false,
  error: '源客户端数据库修改当前已禁用，尚未完成独立安全审查与授权流程'
}

// Keep source-client writes disabled until a separate reviewed approval and backup flow exists.
function deniedHandler(_operation: MutationOperation) {
  return async () => ({ ...unavailable })
}

export function registerSourceMutationHandlers(ipcMain: IpcRegistrar, mutations: SourceMutations): void {
  ipcMain.handle('chat:updateMessage', deniedHandler(mutations.updateMessage))
  ipcMain.handle('chat:insertTextMessage', deniedHandler(mutations.insertTextMessage))
  ipcMain.handle('chat:deleteMessage', deniedHandler(mutations.deleteMessage))
  ipcMain.handle('chat:installAntiRevokeTriggers', deniedHandler(mutations.installAntiRevoke))
  ipcMain.handle('chat:uninstallAntiRevokeTriggers', deniedHandler(mutations.uninstallAntiRevoke))
  ipcMain.handle('sns:installBlockDeleteTrigger', deniedHandler(mutations.installMomentsBlock))
  ipcMain.handle('sns:uninstallBlockDeleteTrigger', deniedHandler(mutations.uninstallMomentsBlock))
  ipcMain.handle('sns:deleteSnsPost', deniedHandler(mutations.deleteMomentsPost))
}

export function denySourceMutationRoute(
  _operation: MutationOperation,
  respond: (status: number, message: string) => void
): void {
  respond(403, unavailable.error)
}
