# 邮件接入

在 WebUI 的「数据源 → 邮件 → 配置」中设置邮箱，保存后开始同步。邮件会出现在邮件平台筛选、全局搜索、会话和导出中；MCP 的既有查询工具也接受 `platform=email`。

## 账号和认证

当前配置一个邮箱、一个文件夹（默认 INBOX），已归档数据按账号隔离。通用 IMAP 使用服务商提供的主机、TLS 端口（通常 993）和授权码/应用密码。连接验证服务器证书，不支持明文 IMAP。

Gmail 和 Outlook 提供服务器预设，并支持 XOAUTH2 access token。当前版本需要自行取得有效 token 后填入，尚无浏览器一键登录或 token 自动续期；过期后需更新配置。Gmail 可在账号策略允许时使用应用密码。不要把普通网页登录密码当作通用授权码。

官方认证说明：[Gmail IMAP](https://developers.google.com/workspace/gmail/imap/imap-smtp)、[Microsoft IMAP OAuth](https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth)。账号权限和服务商策略仍决定能否连接。

凭据通过 Windows 当前用户 DPAPI 加密保存在消息中枢本机存储目录，状态接口不返回密码或 token。切换 Windows 用户后无法直接使用原凭据。

## 同步和归档

同步使用只读文件夹与 BODY.PEEK，不修改已读、删除或移动状态。每轮有条数和大小限制；后续同步继续读取。进度在本地索引成功写入后保存；重复导入按稳定 ID 去重。清空本地索引或恢复备份后重置邮件进度，重新同步补齐。

每轮最多 50 封，在线单封上限 20 MiB；遇到超限或损坏邮件会停止且保留原进度，界面提示原因，可先用本地文件导入归档。文件夹名称当前使用 ASCII（国际化名称需使用服务器的 IMAP 编码）。

正文包括主题、发件人、收件人、抄送和附件文件名，可以统一搜索。HTML 邮件转为文本，不加载外部图片或执行网页内容。当前不下载附件，不提供邮件发送、服务端标记或多邮箱并行配置。

也可在「导入记录」选择 EML 或 MBOX，预览校验后创建导入任务，默认平台选择「邮件」或「全部平台」。建议填写邮箱地址作为账号，与在线同步使用相同标识。日期缺失的邮件以时间 0 保存并提示，不猜测时间。

## 验证边界

测试使用合成邮件及模拟 IMAP，不读取真实邮箱，不发送邮件。真实账号登录、服务商授权和长期同步需配置账号后单独验收。
