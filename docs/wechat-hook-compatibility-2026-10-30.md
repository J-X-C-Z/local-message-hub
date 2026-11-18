# WeChatAgent 旧版 Hook 兼容性实测

## 结论

`WeChatAgent 0.4.1+` 随包分发的旧版 `version.dll` Hook 不能直接用于微信
`4.1.15.13`。旧 Hook 只在它自带的 `4.1.10.27` 客户端上成功初始化和监听；
新版进程加载的是 `C:\Windows\System32\version.dll`，本地 Hook 没有被加载，
也没有创建 HTTP 监听。

本机现已保留原 `4.1.15.13` 安装，并在忽略目录
`.runtime/wechat/4.1.10.27-hook` 使用已校验的 `4.1.10.27` 客户端、旧 Hook
和随机回环令牌。桌面入口为 `微信 4.1.10.27 Hook.lnk`。这是旧版 Hook 可
正常工作的实际运行路径；中枢发送端优先使用该 Hook，并通过本地数据库新
回执确认投递。

## 实测对象

- 发行资产：`WeChatAgent-Setup-0.4.1+-x64.exe`
- SHA-256：`6957dc2b2892c8e86db01c6d416f2a4c3d1ab461ddb5ad7c5d6379558cae61b8`
- Hook SHA-256：`e30315def563b410c39d6de8932919013a1c95407e2a2e1a4d74607df7d744eb`
- 自带微信：`4.1.10.27`，腾讯 Authenticode 签名有效
- 当前微信：`4.1.15.13`

## 结果

在隔离目录启动自带 `4.1.10.27` 后，Hook 健康接口返回：

```json
{"backend":"wechat-hook","initialization":"deferred-module-v3","nativeFileSend":true,"nativeStickerSend":true,"startupState":"ready","version":"4.1.10.27"}
```

发送接口在没有已登录账号时按设计返回：

```json
{"status":409,"body":{"ret":1,"error":"Version or account mismatch"}}
```

把同一 Hook 放到当前 `4.1.15.13` 隔离副本的根目录和核心 DLL 同目录后，
进程模块表仍只有系统 `version.dll`，Hook 没有生成初始化日志，`30012`
端口没有监听。源码覆盖层也明确要求 `CoreVersion() == "4.1.10.27"`，其他
版本直接拒绝发送。

## 边界

本次没有向真实会话发送消息，也没有验证当前账号登录后的旧 Hook 行为。
旧客户端使用独立程序目录，但共享现有本机账号数据；切换前已复制数据库和
最小配置到 `work/wechat-switch-backup-20261030`。原
`C:\Program Files\Tencent\Weixin` 没有被替换。
