# 003：隔离的原生归档与桌面插件实时事件

本阶段提供 Rust 自有归档中真实的、已经提交的本机写入→事件链路，不是拿旧快照
行模拟「实时」。当前消息写入来源是具有专用权限的生产者对本机接口的请求；
未接入真实微信、企微、QQ 或邮件源。现有 Python profile、启动器及实际账户
数据仍未迁移，不能互换。Windows/真实账号未验证。

## 启动及授予权限

使用与旧配置目录无关、私有且空的新目录：Unix 使用 mkdir -m 700；
Windows 开始前由操作者确保目录及继承 ACL 仅允许合适的本机用户。
宿主不会迁移或接管现有目录。授权 JSON 放在目录**外**，以免被误识别为外部文件。
以仓库根目录为起点：

    cargo build --manifest-path rust-hub/Cargo.toml --locked
    BIN="$PWD/rust-hub/target/debug/local-message-hub-rs"
    data="$(mktemp -d)"
    chmod 700 "$data"
    "$BIN" init-live --directory "$data"

对于以历史翻页为主、同一时间戳下消息较多的**新普通归档**，也可以改用：

    "$BIN" init-live --directory "$data" --history-index

这个可选开关用含消息 ID 的索引替换默认的会话索引，有利于按时间戳与 ID
排序和续页。默认仍使用原索引；参数只接受空目录，不迁移既有归档，不适用于
受保护归档。接口、游标和精确总数不变。较宽的索引可能增加磁盘用量和写入开销。
目前只做了 Linux x86-64 上的合成数据验收。

如果新归档经常按账号浏览较长的会话列表，可以独立选择：

    "$BIN" init-live --directory "$data" --conversation-index

这个选项增加账号内按最近时间和会话 ID 排序的索引，保留原索引。
可以和 `--history-index` 一起使用，也可以只启用其中一个；每个选项只能传一次。
它不改变会话列表的顺序、总数或偏移量，可能增加归档体积和写入开销，因此默认关闭。
仅适用于新普通归档，不会给旧归档或受保护归档加索引。

分别用 new-plugin-token 为桌面与生产者生成不同的 token。授权结构示意：

    {"version":1,"grants":[
      {"id":"example.desktop","kind":"desktop","token_sha256":"<desktop hash>",
       "permissions":["conversations.read","messages.read","events.read"]},
      {"id":"example.reader","kind":"producer","token_sha256":"<producer hash>",
       "permissions":["messages.ingest"]}
    ]}

    "$BIN" serve --live "$data" --grants /path/to/private/plugins.json

仍只监听 127.0.0.1。桌面端不能写入；读取生产者不能用其凭据读消息或订阅。
角色区分只是服务权限，不改变客户端进程的操作系统权限。
生产者当前是可信的归档授权方，不能局限到某个账号。已有消息标识的归属
绑定首次写入的生产者，不同生产者不能覆写；正式源注册及分账号隔离还未实现。

## 写入与读取

POST /api/v1/ingest 只接受显式认证的应用 JSON，格式示例（内容为合成记录）：

    {"idempotency_key":"fixture-1","messages":[
      {"platform":"wechat","account_id":"fixture-account","conversation_id":"fixture-room",
       "message_id":"fixture-message","title":"示例会话","sender_id":"fixture-person",
       "sender_name":"示例","timestamp":1770000000,"is_self":false,"text":"第一条"}
    ]}

只写标准化归档，不发送真实消息，不将文件、路径或凭据交给平台。生产者重试失去
回执的相同提交时，用同一 idempotency_key 与同一消息批次；返回原提交回执。
同一 key 不同内容、跨生产者覆写、或改已有消息的会话/方向会冲突。
允许同一生产者以不同 key 修订同一条入站消息。响应为 created/revised/unchanged
计数和该次提交的 checkpoint 值。刚提交的记录可经原生 GET /api/v1/messages 读回。
这是有界普通文本批处理，不是完整原 Hub 的媒体、同步、导入或发送 API。

## 桌面加载与 SSE 恢复

接口需要原生 HTTP 客户端加 Authorization bearer；浏览器原生 EventSource 不能
设置此头，切勿把 token 放进 URL。桌面可从 manifest 发现 endpoints：

1. **先保存** GET /api/v1/events/checkpoint 返回的 cursor，再拉会话与消息。
   初始读取与实时波次之间发生的提交不会漏，但可能同时出现在初始快照与事件；
   以 (platform, account_id, conversation_id, message_id) 合并/重新读取，不追加重复行。
2. GET /api/v1/events/stream 随 Last-Event-ID 传已应用的 cursor（或使用 cursor
   查询参数，两者不可并用）。不传则从连接时当前水位开始，不重放历史。
3. message.created 或 message.revised 只包含事件编号、类型、平台、账号、会话、
   消息标识、时间，不传正文、标题、发送者或附件。应用/刷新收到的事件后才保存
   id，断线后用最后已应用的 id。设计允许在应用与持久化之间发生重放，客户端要幂等，
   不承诺端到端严格一次。
4. 也可调用 GET /api/v1/events?cursor=...&limit=100 分页补齐；使用 next_cursor
   继续。当前服务会限制流数量，闲时轮询约 250 ms，连接约五分钟主动断开可重连。

示例把初始加载与游标保存放在一个最小插件里（只打印元数据，不处理 UI）：

    python3 rust-hub/examples/desktop_plugin.py       http://127.0.0.1:实际端口 --cursor-file /path/to/private/desktop.cursor

游标绑定独立初始化的归档标识，进程重启后同一归档可续读。超过水位或来自不同
归档的游标会被拒绝。不支持对数据库进行手动复制/回滚/替换后依旧无损续读：
完整复制可能连归档标识一起复制；有需要应显式重新建立快照和 cursor。目前无
原生恢复功能、事件清理、旧 SSE 平台/静音过滤、原应用锁、通知偏好或完整旧接口兼容。

## Upgrade account-scoped event replay from a 003 native archive

New native directories initialized with rebuild-001 already contain the
account event index. An existing 003 **Rust-owned native archive** remains
readable/writable with unscoped clients. Scoped event queries on it initially
return 503 with `account_event_upgrade_required`. To enable scoped replay,
stop Hub and any other archive writers and keep a verified copy of the
quiesced native directory. Run:

```sh
local-message-hub-rs upgrade-event-scopes --directory /path/to/rust-native-directory
```

This explicit command checks the Rust-native identity, backfills per-account
ordinals in one SQLite transaction, installs a trigger for subsequent events,
and preserves existing event IDs, archive identity and receipt contents. It
can be run again and does not open a legacy Python index for writing. Keep
sufficient temporary/disk space for the additive index and transaction. A
scoped desktop never receives an archive-wide checkpoint, even when multiple
accounts' events interleave; unscoped trusted grants retain the 003 behavior.

## Enable metadata editing on existing native archives

New Rust-owned archives are initialized with the metadata audit schema.
Older Rust-owned archives can enable the new capability explicitly: stop the
server/writers, preserve a verified quiesced copy, then run
`local-message-hub-rs upgrade-metadata --directory <existing-native-directory>`.
The transaction adds an empty audit table/index; existing metadata remains
at revision zero until edited. Repeating the upgrade is safe. The original
archive identity, global event journal and account-local event sequence are
not changed. Existing protected directories remain operator-owned; this
does not defend against a local administrator who manually changes SQLite
internals. Neither a detached snapshot nor an active Python profile is a
valid upgrade target.
