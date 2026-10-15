# WeFlow 功能差异矩阵

审计日期：2026-10-12。上游基线为 [`hicccc77/WeFlow`](https://github.com/hicccc77/WeFlow)

2026-10-13 更新：QQ 全局缓存媒体视图加入 UTC+08 日期范围；vendored QCE 源码先筛选元数据再统计和分页，并返回能力标记。Windows 打包流程现从 vendored 源码和 Cargo.lock 构建 QCE；p260 包内 QCE（SHA-256 `adf2db90cd93d508a6d8c9e739fa22d68a37e547601757ee311c8c0f7f1b2107`）和 p263 包内 QCE（SHA-256 `fe8831b62a2c311e08cb3bdfbb3f39535b0a47f01502f6dbd50304ea89660386`）有隔离 loopback/日期筛选运行验收记录。p262 包内 QCE（SHA-256 `481999bc4de1c59e01e6b04539e2ba770e4f5cfbf999f95161968f73878bcc45`）已通过源码能力检查、构建和包内隐私检查，但未对该哈希独立运行日期筛选 smoke。p263 最终包在冒烟后重新执行隐私扫描并通过。P14 hosted build、artifact 往返先后由 run 38547565569 和当前提交上的 run 38550004873 验收通过；当前 artifact 为 251,741,288 bytes，保留至 2026-10-21。真实账号缓存验收仍未完成。本地哈希仅作诊断，不作为发布 allowlist。
P11 本机头像读取补充：微信 `head_image` 表存在同一 username 的多条缓存记录时，按 `update_time` 从新到旧选择首个签名有效 JPEG，避免后写入的旧记录覆盖新头像；不访问远程头像源。合成 SQLite 回归覆盖乱序重复记录。
`main`，commit `2f23e72cebf7f309a0f4d129307d16d7b0cd41c3`（与本仓库
`UPSTREAM.md` 固定版本一致，包版本 5.0.0）。Local Message Hub 对照
`main` commit `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`；当前工作区另有
未提交改动，矩阵按已检查的工作区代码记录，不把文件存在当作功能完成。

状态含义：**已实现** 表示统一 Hub 流程具备对应能力并有测试；**部分实现**
表示仅覆盖明确列出的本机范围；**未实现** 表示尚未进入统一 WebUI/Hub；
**不适合移植** 表示需独立评估，不能为了功能对齐直接复用其副作用。

2026-10-14 更新：QQ 全局缓存资源库的类型筛选仅显示 QCE 实际支持的图片、视频、
语音和文件；微信聊天媒体仍可筛选本机缓存表情。WebUI 回归覆盖两种筛选集合。
同日，微信缓存 GIF 只允许 trailer 后跟零填充，拒绝 trailer 标记后的非零尾部数据；
缓存完整性合成回归覆盖合法与截断数据。
同日，WeChat `local_type=11000` 与现有 47 表情码统一进入贴纸占位和筛选/计数；
游标与偏移同步现在都只提取消息 XML 中通过 32 位十六进制白名单的 emoji MD5，
复用账号绑定缓存解析器。当前没有已验证的真实 type-11000 缓存条目，故真实预览仍待验收。
同日，QQ 全局缓存图库的可见图片卡片加入账号绑定的不透明资源 ID 懒加载预览，
复用现有并发限制、同源缓存 token 和翻页取消。真实 QQ 缓存格式与设备覆盖仍待验收。

| 功能 | WeFlow 上游入口 | Hub 当前实现与状态 | 复用/适配方向 | 验收方法 |
|---|---|---|---|---|
| 会话、消息浏览与条件搜索 | `src/pages/ChatPage.tsx`、`electron/services/chatService.ts` | `hub/store.py`、`hub/server.py`、`webui/app.js`；三平台统一索引、分页、过滤及跨平台搜索；搜索结果的会话头像优先使用会话行字段，缺失时按精确平台/账号/会话 ID 回退到联系人头像；微信引用消息可用精确来源 `server_id` 跳转到同账号、同会话内唯一匹配的原消息，并在对应本地媒体缓存存在时以内联预览呈现图片/视频/语音；跨账号、歧义、缺失缓存保持占位，**已实现** | 保持 Hub 规范模型和三平台适配器，不接入 WeFlow 主进程；头像仍只经本机已有缓存提供 | 合成数据覆盖三平台、同 ID 碰撞、分页/筛选/上下文和 UI API；搜索头像覆盖联系人回退、会话头像优先级与跨账号隔离；微信引用跳转覆盖来源 ID 精确匹配、同账号/会话隔离与歧义拒绝；引用媒体覆盖唯一来源匹配、缓存缺失占位及本地同源 URL |
| 联系人、群聊和头像 | `src/pages/ContactsPage.tsx`、`electron/services/contactCacheService.ts` | `store.py` 联系人查询、带头像的 WebUI 和 capability-gated 联系人 JSON/CSV 导出；联系人导出仅保留规范化资料字段白名单，丢弃 QCE `raw`/未知嵌套 payload、URL 和 token/凭据值；群成员 modal 支持三平台本机 reader/QCE 数据的 JSON/CSV 导出（严格复用已索引账号/群校验，单次最多 1,000 条，超限返回 413；只输出白名单字段与来源，不带头像 URL/token 或 provider 扩展字段）；WeChat reader 从实际 contact 表选取 WeFlow 白名单头像 URL 字段，拒绝非允许域名且绝不联网。`head_image.db` 已通过只读 schema/签名审计：精确按联系人 `username` 匹配 `head_image(username, md5, image_buffer, update_time)`，不推断 md5；只接受已加载密钥、精确 schema、JPEG 签名和有界图片，原子写入 Hub 私有缓存，本地头像优先于 URL。QQ reader 在选定账号根下只读至多八个头像文件，拒绝符号链接/越界路径并限制每项 2 MiB，再由 Hub 校验图片签名和写入自有缓存；企微会话头像从本轮解密快照里精确读取 `avatar_store_v3.db` 的 `conversation_avatar_table`，只匹配会话 ID、限制图片大小并写入 Hub 自有缓存；联系人头像仍只有 URL 元数据。MCP 联系人列表仍只返回头像引用；显式 `get_contact_avatar` 按平台/账号/联系人精确身份读取 Hub 已缓存图像并以 MCP image content 返回，不联网且缓存未命中即失败。WeChat/QQ 本机抽样成功返回缓存图片；真实 WebUI 像素验收、其他 schema、公众号文章/完整资料仍待验收，**部分实现** | 复用标准联系人模型和经过核实的字段白名单；只读源头像库并写 Hub 自有缓存；不下载远端头像 | 合成联系人分页/导出、跨平台身份隔离、头像 URL 字段候选、恶意近似域名拒绝、嵌套 `raw`/未知字段/URL/token/凭据剔除、群成员账号/群校验、导出 gate/来源、CSV 公式注入、字段白名单和 1,000 条超限拒绝；头像有精确 username/schema、缓存键身份、图片签名、大小/批次和源库只读合成覆盖；真实 WebUI 显示和其他版本 schema 仍待验收 |
| 图片、视频、实况与媒体资源 | `src/pages/ChatPage.tsx`、`src/pages/ResourcesPage.tsx`、`electron/services/imageDecryptService.ts`、`electron/services/snsService.ts` | 微信 V1/V2 本机图片解密、WeCom/QQ 可用缓存解析及统一预览已接入；普通 WeChat 预览读取 mid 档，明确导出优先使用已缓存完整原图，再退化到 mid/thumbnail；媒体库按附件项支持平台、类型、会话关键词和 UTC+08 日期范围筛选与稳定分页，关键词也匹配聊天正文及发送者名称/ID；另可分页读取当前已认证 QCE 的全局缓存资源，UI 只接收随机句柄/安全元数据，预览与批量 ZIP 由服务端验证账号和大小后读取；路径严格按缓存类型与文件名编码，不返回 QCE URL 或本机路径。SQLite JSON1 将企微/QQ附件展开后再做类型筛选和 COUNT/LIMIT/OFFSET，普通微信媒体仍以消息为一项；WeChat AES key 不做进程扫描，离线密钥缓存仅写 Hub runtime。WeCom 消息多附件可在媒体库逐项选择和批量导出；聊天气泡现按 `resource_index` 逐项显示企微与 QQ 附件，并通过只读 resolver 单独打开；朋友圈图片/视频与 Live Photo 静态帧+视频仅从既有缓存离线预览；实况 MP4 走签名和缓存目录校验，MCP 通过短时本地 token 显式返回缓存图片或 MP4/WebM 视频（视频为嵌入式 resource，限制 8 MiB），拒绝远程 URL 与任意路径；WeChat 47/11000 表情均可占位并参与贴纸筛选，11000 仅用消息 XML 中白名单验证的 MD5 查找账号绑定缓存，真实缓存条目尚未验证；全平台完整资源库与全格式批量解密仍未接入，**部分实现** | 复用各平台只读 resolver；任何缺失缓存显示不可用，不猜路径、不改源库，不请求 CDN | 合成 V1/V2 图片覆盖格式、页脚、best-tier 原图选择/回退和密钥隔离；WeCom 多附件 UI 测试核对每项序号与 QQ 回归；QQ gallery 覆盖分页筛选、opaque ID、账号绑定、URL/路径不泄露、错误类别与大小拒绝；媒体日期边界及组合筛选、API MIME/权限/超限/缺失均用合成数据覆盖；朋友圈覆盖图片/视频缓存边界、签名、过期 token、显式 MCP 图片/视频请求和 capability gate、Live Photo UI；关键词组合按标题/正文/发送者/附件名覆盖跨来源 COUNT 和分页；不以真实缓存作测试夹具 |
| 防撤回 | `electron/services/chatService.ts`、`electron/wcdbWorker.ts`、`electron/services/wcdbCore.ts` | Hub 的 `anti_recall` capability 默认关闭且由服务端强制不可用，**未实现**。固定版 WeFlow 将安装/检查/卸载委托给仓库未包含的 WCDB 原生组件，无法审计实际触发器行为与回滚语义。 | 该功能会修改源客户端数据库。在原生实现可审计、触发器行为和 schema/版本兼容性验证、且用户单独批准修改源客户端之前，必须保持不可用。需要隔离数据库备份，并通过安装/检查/卸载/幂等/回滚验收；每次操作确认准确账号与数据库。 | 不访问真实数据库。只有拿到原生实现后才可用一次性隔离数据库验证；不能从包装 API 推断其行为。 |
| 实时桌面通知 | `src/pages/NotificationWindow.tsx`、`electron/services/systemNotificationService.ts`、`electron/services/messagePushService.ts` | `notifications` 默认关闭；授权后使用 loopback-only SSE 推送新入库来信元数据，支持平台筛选、全部/白名单模式、会话白名单与静音黑名单、标准/静音提示、点击打开会话，通知不含正文；运行中的 SSE 每轮重新验证 capability/app-lock，撤销权限后关闭；**本地 WebUI 子集已实现** | Hub 索引只读轮询；浏览器需用户手势授予桌面通知权限；新连接以当前 rowid 为基线，不回放已有历史。暂不提供远程/webhook 推送，也不弹出前台页面通知 | 合成 HTTP 流覆盖 capability gate、平台/白名单/黑名单过滤、首连基线、无正文载荷和已建立流撤权后停止推送；浏览器验收覆盖授权与点击定位 |
| 私聊统计 | `src/pages/AnalyticsPage.tsx`、`src/pages/ChatAnalyticsHubPage.tsx` | `hub/analytics.py`、`/api/analytics/private`、WebUI“私聊画像”；会话量、发收比例、媒体和最近活动，**已实现** | 统一索引重建图表，不复用绑定 WCDB 的服务 | `hub.test_analytics` 与 HTTP 合成 fixture 核对计数/身份 |
| 群聊统计与成员画像 | `src/pages/AnalyticsPage.tsx`、`electron/services/chatService.ts` | `/api/analytics/groups`、WebUI 成员排行；区分 roster 人数与索引中观察人数，**已实现** | 当前观察到的消息发送者画像；不推断缺失成员活动 | `hub.test_analytics` 覆盖排序、联系人显示名与自发消息 |
| 年度报告 | `src/pages/AnnualReportPage.tsx`、`electron/services/annualReportService.ts` | `/api/reports/annual`、WebUI 年度标签；含月/时/类型、星期×小时活跃热图、最长连续活跃日期及年度边界；CSV 包含界面报告数据，**本机索引子集已实现** | 统一索引、UTC+08 日历边界，允许平台/账号/会话范围；全时间报表仅聚合已活跃日期 | `hub.test_analytics` 覆盖范围/时区/连续日，HTTP API、热图/连续日 CSV 与 WebUI 渲染断言 |
| 双人报告 | `src/pages/DualReportPage.tsx`、`electron/services/dualReportService.ts` | `/api/reports/pair` 与会话画像入口；按平台/账号/会话隔离，**已实现** | 不跨平台或账号合并身份；缺少接收者 ID 的历史数据以消息发送者派生对象 | 合成双向对话、日期范围与不存在会话 HTTP 404 测试 |
| 聊天记录多格式导出 | `electron/services/export/` 下 `ExportOrchestrator.ts`、`formatters/`；UI `src/pages/Export/` | `hub/exports.py` 和统一 API/UI 支持 JSON、CSV、Markdown、TXT、HTML、XLSX、SQL、ChatLab JSON/JSONL，**格式覆盖已实现** | Hub 自有标准库 renderer；ChatLab envelope 参考上游语义，不复制 WeFlow formatter 源码 | `hub.test_store` 覆盖 Unicode、来源身份、公式注入、HTML 转义与 SQL 隔离 |
| 自动化/批量导出 | `src/pages/Export/components/Automation/`、`src/pages/Export/hooks/useAutomation.ts`、`electron/exportWorker.ts` | `hub/auto_export.py`、设置页与受 capability 保护的 API；支持本机目录、间隔/平台/格式、立即运行、取消、原子输出、重启恢复和单次 250,000 条上限，**本地合成范围已实现** | 使用 Hub 统一索引和现有导出 renderer；关闭模块会停止任务，不接触源平台数据库 | `hub.test_auto_export` 与 `hub.test_server` 覆盖持久化、gate、格式、取消、大小限制及本机输出 |
| 朋友圈浏览、搜索、导出 | `src/pages/SnsPage.tsx`、`electron/services/snsService.ts` | `integrations/wechat/moments.py` 对识别的本机 `SnsTimeLine` schema 做有界只读解析；独立 capability 控制 HTTP、MCP 和 WebUI，支持作者/正文/评论/日期筛选、分页及全量筛选 JSON/CSV/Markdown/TXT/HTML 导出，搜索不匹配用户名或媒体 URL，**部分实现**。作者头像仅从精确匹配的 `wechat + account_id + username` 联系人索引读取，再经同源缓存代理显示；不从 Moments XML 猜 URL，也不请求网络。WeFlow 兼容缓存 resolver 只读已有文件，校验图片/视频签名和缓存目录边界，包含 Live Photo；UI 通过同源短期 token 显示/播放，MCP 只在显式调用时返回 <=8 MiB 的缓存图片或 MP4/WebM 视频（视频以嵌入式 resource 返回）；不抓取远程媒体。评论 `refCommentId` 对应关系显示为转义的回复作者标签；悬空引用按普通评论渲染。合成账号隔离及 UI 测试通过；真实账号 schema、头像和真实缓存仍未验收 | 保留 Moments 独立模型，不伪装成聊天消息；头像仅复用已索引、同账号联系人并走本机代理；不抓取远程媒体 | 合成 SQLite/XML 覆盖筛选、多格式全量导出、互动/地点保留、评论回复及缺失引用、HTML 转义、CSV/HTML 注入防护、媒体凭证剥离、缓存 URL 规范化、签名/符号链接拒绝、MCP 显式图片/视频回传和 capability gate、Live Photo UI；头像覆盖同账号命中/跨账号与缺失回退；另需真实 schema 与缓存的用户验收 |
| 朋友圈防删/隐藏拦截 | `src/pages/SnsPage.tsx`、`electron/main.ts` `sns:installBlockDeleteTrigger` IPC | Hub 无对应功能，**不适合直接移植** | 属于源客户端行为拦截与数据库副作用；需单独安全、合规、备份审查，不能跟普通浏览开关捆绑 | 不进入常规对齐验收；只有独立批准和隔离数据库测试后再决定 |
| 本地消息编辑、新增、删除 | `electron/main.ts` 的 `chat:updateMessage`、`chat:insertTextMessage`、`chat:deleteMessage`；`electron/services/chatService.ts` | Hub 原始平台记录保持只读；仅允许编辑 Hub 自有备注/标签，**不支持修改源消息** | 如需支持，只操作可逆的 Hub 覆盖层并保留原值；源库编辑另设明确授权、备份和审计 | 先测覆盖层可撤销、审计和源消息不变；禁止拿真实会话做自动测试 |
| HTTP API 与新消息事件推送 | `electron/services/httpService.ts`、`electron/services/messagePushService.ts`、`docs/HTTP-API.md` | Hub 有 loopback REST、MCP 查询与管理审计，并新增仅供本机桌面通知使用的 body-free SSE；不提供远程 webhook 或源客户端实时 push，**部分实现** | 沿用 loopback 主机校验、同源校验、notifications capability gate；SSE 元数据不记录到日志 | 合成 HTTP 流测试覆盖禁用拒绝、平台/会话过滤、首连基线、正文排除；合成 WebUI 测试覆盖点击后平台/账号/会话精确定位及通知无正文；真实浏览器/系统通知点击仍需运行时人工验收 |
| 资源管理、批量解密 | `src/pages/ResourcesPage.tsx`、`electron/services/imageDecryptService.ts` | 媒体库现支持跨页选择最多 100 个独立附件（含 QQ 漫游资源、QCE 当前账号全局缓存资源）、resolver 本机 ZIP 打包、进度/取消、来源 manifest；QQ 资源序号支持 0-100，与只读 provider 对齐；QCE gallery 仅传递 account-bound opaque identity，资源 URL 保留在服务端，路径按类型/文件名重建与编码，下载受 50 MiB 单项与 512 MiB 总预算约束；WeChat 图片 AES key 只进入 Hub runtime cache，媒体路径禁用图片密钥进程扫描；清理本地索引会删除 Hub 管理的附件归档；服务端限制单文件 50 MiB、总量 512 MiB，流式复制逐块执行限制并检查取消，媒体 capability 关闭时拒绝；独立可选备份 ZIP 可附带快照引用的 Hub QQ 漫游媒体，恢复前校验路径、预算与 SHA-256 并拒绝冲突覆盖；**批量打包、有限 QQ 媒体备份及 WeChat V1/V2 离线解密已实现，完整资源库/全平台批量解密仍部分实现** | 只接收平台/账号/会话/消息/可选资源序号或服务端签发的随机资源句柄，不接受客户端 URL/路径；数据由现有平台 resolver 取得，源平台仍只读。仅纳入本机可解析附件，不保证恢复未缓存或不支持格式；媒体备份不含源客户端数据库、缓存、凭据或临时 JSON | `hub.test_resources`、`hub.test_backups`、`integrations.wechat.test_media` 与 HTTP 合成测试覆盖 manifest、部分失败、超限/动态增长、分块取消、能力开关、下载、QQ roaming/gallery opaque identity、URL/路径不泄露、图片格式/密钥隔离、备份引用文件筛选/跨目录恢复/路径摘要校验/冲突保护和索引清理；真实私有附件不用于自动测试 |
| 语音转写 | `src/components/VoiceTranscribeDialog.tsx`、`electron/services/voiceTranscribeService.ts` | Hub 增加默认关闭的本机转写 capability 和消息内入口；只接受平台/账号/会话/消息复合身份，由已同步 provider 解析本机语音，不接受任意 URL/路径。仅支持本机已安装 Sherpa Node 运行时及 WeFlow SenseVoice 模型下的 16-bit PCM WAV；模型/运行时缺失明确显示 unavailable，不下载；转写文本仅存于 WebUI 当前内存且不写回消息或文件。当前环境未安装运行时和模型，**本地合成路径已实现，真实推理待可选模型验收** | 保持 AI 中立；只调用已安装本机运行时与固定本地模型目录。拒绝未索引消息、非语音、缓存缺失及其他 WAV 编码/格式，不接触远端服务 | `hub.test_voice_transcription` 与 `webui/test-voice-transcription-render.cjs` 覆盖 capability、provider-only 音频输入、复合身份、缓存缺失、无效音频、无网络模型检测、UI 调用/结果显示及源数据不变；真实本机 SenseVoice 推理仍待本机模型验收 |
| 本地备份 | `src/pages/BackupPage.tsx` | Hub SQLite 在线备份/校验/恢复已实现；可显式打包快照引用的 Hub 管理 QQ 漫游附件及按账号隔离的微信媒体缓存，并在恢复时校验 manifest、路径和 SHA-256、拒绝覆盖冲突文件；企微源缓存及原始平台数据库不纳入，**限定范围部分实现** | 仅包含快照确实引用的 Hub 缓存文件；排除旧版未按账号隔离的微信缓存、孤立缓存目录、QCE 临时 JSON、账号凭据、企微源缓存和源数据库 | `hub.test_backups` 与 HTTP 合成测试覆盖引用文件筛选、账号隔离、跨目录恢复、路径/摘要校验及冲突保护；不使用真实私有附件 |
| 账号管理、应用锁定 | `src/pages/AccountManagementPage.tsx`、`src/components/LockScreen.tsx` | `account_safety` 默认关闭；启用后以 PBKDF2 保存口令校验值，循环本机 WebUI/API 受门禁保护，闲置 15 分钟锁定、5 次失败限流、重启后必须重新解锁；独立 MCP stdio 不受保护，**WebUI 范围部分实现** | 继续保持平台账号和源库只读；若要求 MCP 也受锁，需要独立的 MCP 客户端授权/解锁设计 | manager/API 合成测试覆盖哈希、限流/会话、绕过 UI 读取、媒体访问、锁定、解锁和关闭；浏览器运行时验收待本机预览 |
| 关系足迹与成就 | `src/features/relationship-achievements/`、`electron/services/chatService.ts` | 独立默认关闭的 `relationship_footprint` 开关、只读 API 与统计标签；按日展示发收量/媒体量，并为私聊提供首条消息、总量、活跃日、连续日、媒体量和跨年客观里程碑；里程碑的媒体类别识别企微附件、QQ 漫游资源及旧版 QQ 媒体别名（按 type/kind/k 解析），按消息每类只计一次；不做情绪/关系推断、不含消息正文，**客观子集已实现；上游语义旅程成就未移植** | 基于平台/账号/会话复合身份和 UTC+08 日期边界派生；群聊不显示双人关系成就 | `hub.test_analytics` 与 HTTP 测试覆盖身份隔离、UTC+08 边界、跨年/连续日、群聊限制、能力门禁及无正文响应 |
| AI 见解、内置 Agent | `src/pages/agent/`、`electron/services/agentModelLedService.ts` | Hub 按产品决策不内置模型、RAG 或 Agent；MCP 读工具可供外部 Agent 使用，**有意不原生移植** | 不引入 AI 运行时；继续扩展受权限控制的 MCP 工具即可 | 检查 AI 功能入口不存在；MCP 的授权、隐私和内容限额测试 |
| QQ 真实消息发送 | WeFlow 不提供真实 IM 对端发送；`chat:insertTextMessage` 是 WCDB 本地写入 | Hub 有 text-only 服务、NapCat OneBot 11 loopback+token sender、QQ 登录账号绑定、人工 UI 审批和审计，NapCat配置通过同目录临时文件原子替换，**实现及合成测试完成；真实设备验收待用户执行** | QQ 账号必须与审批时本机登录账号完全匹配；超时/中断为 unknown，不自动重试；MCP 只准备与查状态 | NapCat adapter 使用 fake opener；服务/API/MCP 使用合成适配器，配置写失败回归保护原文件。真实私聊及群聊须由用户在本机明确确认 |
| 微信/企业微信真实发送 | WeFlow 没有可复用的本机真实发送适配 | 个人微信取消“只读”限制新增默认关闭的 `wechat_send` 能力：Win32 `ctypes`拨打已打开的聊天窗口并输入文本，没有注入、DLL hook 或客户端库写入；传入回读只读缓存消息行，必须找到本机账号发出的同文本才认定送达（否则 `unknown`，不重试）。企微具备默认关闭的独立机器人身份 Markdown、图片、文件、AMR 语音、视频通道，Windows 当前用户 DPAPI 凭据存储、本机逐条审批和 unknown 不重试均已实现，**两者实机送达均待验收**。当前 Windows 已用临时合成凭据执行真实 DPAPI protect/unprotect 往返，并确认密文不含明文 secret。附件仅来自本机文件选择器，API 不接受路径；按扩展名、签名、大小校验并在发送前重验。不代表当前员工身份；MCP 仍只提供文本待审批请求创建与状态查询，不可上传附件、审批或投递 | 使用仓库内标准库 TLS WebSocket 上传 init/chunk/finish，再以返回 media_id 发送；目标须在机器人可达范围。机器人发送和 QQ 发送走隔离 API/adapter，不能交叉审批 | 合成 HTTP 路由、文件签名校验、适配器及 MCP 测试验证默认关闭、身份标签、凭据不回显、单次审批、附件数据不泄露、跨平台隔离、状态隔离及超时不重试；Windows `integrations.wecom.test_bot_credentials` 使用临时目录实测当前用户 DPAPI 往返；MCP 测试确认 prepare 不投递且缺少能力/身份时不可用；真实机器人授权、目标可达性和设备回执仍需现场验收，不把测试桩视作送达 |

## P11 联系人/公众号增量

WeChat 联系人现按固定 WeFlow `chatService.ts` 规则分类公众号：username 必须
以 `gh_` 开头且 `local_type` 为 1；读取同一本地 contact 数据库中可选的
`biz_info` 表，把 `type` 0/1/2/3 归一为订阅号/服务号/企业号枚举。Hub API、
WebUI 筛选与联系人 JSON/CSV 导出仅处理规范类型，不暴露原始 `local_type`、
`biz_info` 字段或未知 payload。当前可导出已索引的公众号联系人资料，不包括
公众号文章历史或完整资料字段。若表/列不存在则保留普通联系人行为。合成
provider、store、API 和导出测试通过；真实账号 schema 验收仍未完成。

联系人列表的稳定分页排序在姓名和联系人 ID 后追加平台、账号作为复合身份
平局键；因此跨平台/账号复用同一联系人 ID 时，合成数据的分页顺序仍确定。

微信群成员 API 现在为当前分页成员批量关联同一微信账号下已索引的头像引用，
不从源库补查头像，也不请求头像网络地址；弹窗复用本机头像代理渲染。合成
HTTP 测试覆盖同 ID 的另一账号头像不会串用。真实账号头像呈现与更多版本 schema
仍待验收。

## 当前图片导出档位

普通微信聊天图片的批量 ZIP 导出默认选择“最佳可用”，也可选择中图、缩略图
或“仅原图”。这只影响已索引的微信图片消息；WeCom/QQ 附件、朋友圈资源与
其他媒体不接受该参数。解析仅尝试读取本机现存缓存，不触发源客户端下载或修改；
“仅原图”缺失时只将该 ZIP 项标为失败。服务端任务 schema、resolver、manifest
和 WebUI 均有合成测试覆盖。媒体库现可对单张微信聊天图片显式读取本机最佳画质，
只更新所选卡片；缺少该档缓存时保留当前预览，不触发源客户端下载。该增量不关闭
下表记录的全格式解密与资源 parity 缺口。
低于 1 KiB 的 `_h/.h/_hd/.hd.dat` 缓存仅在解密后结构完整时才会被 status、
`original`、`best`、`full` 与原图预检选中；下载中的文件仍使用原有大小/稳定性门槛。
此行为有合成回归覆盖，真实缓存仍待验收。
WeFlow 活跃质量判定未把 `_b/.b/_w/.w/_c/.c` DAT 变体归入原图、中图或缩略图；Hub 暂不读取这些未知档位，
避免在“最佳可用”或显式档位请求中把它们误标为缩略图。已确认的 `_t/.t/_thumb/.thumb` 仍作为缩略图读取。
微信本地类型 47（表情/自定义贴图）仍按 `[表情]` 保留消息上下文；若消息含有效 MD5，
Hub 只查找 WeFlow 已生成的精确账号哈希目录和 `.gif/.png/.webp/.jpg/.jpeg` 文件，供本机
预览、MCP 显式读取及批量 ZIP 导出。未命中、跨账号或路径不安全时维持不可用提示。此处
不读取表情数据库、不访问 CDN、不猜测格式或下载缺失资源；合成测试覆盖格式、路径和账号边界。

QQ 全局缓存资源库的媒体卡片显示只读 API 返回的文件大小，便于预览或批量导出前辨认资源体量；
未知/零大小不显示，UI 不接收 MIME、URL 或本机路径作为展示字段。合成 WebUI 渲染回归覆盖。

## 必须区分的三类“消息操作”

1. **本地数据库编辑**：WeFlow 的 `updateMessage`、`insertTextMessage`、`deleteMessage`
   经 WCDB worker 修改当前客户端的本地库；它不会给聊天对端发送新消息，也不代表
   服务端记录发生变化。Local Message Hub 当前不改原始源库。
2. **Webhook/SSE 事件推送**：WeFlow HTTP API 的
   `GET /api/v1/push/messages` 向订阅者推送新消息/撤回事件，是消息从客户端到外部
   消费者的单向事件通知，不是发送聊天消息。Hub 提供范围更窄的 loopback SSE，
   仅供本机浏览器桌面通知使用，不开放远程 webhook，也不表示源客户端实时推送。
3. **真实 IM 发送**：必须通过平台客户端/授权发送接口把内容投递给明确的账号与会话，
    并处理远端回执。WeFlow 的本地插入和 SSE 推送都不能满足这一语义；Hub 需新建独立发送服务、用户审批、幂等请求和状态审计，默认禁止 MCP 直接投递。
4. **已实现的三条发送通道**：QQ（NapCat OneBot 11）、企业微信机器人和个人微信（Win32 仿人窗口操作）均为默认关闭的独立能力，走同一备案的待审批请求、请求绑定的一次性控制台验证和账号标签；三者互不可交叉审批，也都不向 MCP 暴露审批或投递工具。

## 建议实施顺序

1. P7：本矩阵及基线检查闭合；本次检查发现 `requirements-ci.txt` 漏装
   `cryptography` 和 `pilk-nogil`，已纳入 CI 依赖修复，并在干净临时环境
   中验证依赖清单。
2. P8：建立持久化能力注册表和设置页；服务端/MCP 在每个受限调用点校验，模块关闭
   只关闭入口/任务，不删除已有索引数据。
3. P9：先定义平台适配接口与模拟适配器，接 NapCat OneBot 11 文本发送前验证 loopback
   运行态；用户确认前不得投递。
4. P10：MCP 只创建待审批请求、读取审批状态；不提供绕过 UI 的直接发送工具。
5. P11–P14：按矩阵逐项迁移；对源数据库写入、撤回拦截和客户端注入单独做风险审查，
   不将“源码在仓库”算作统一产品已完成。

P11 资源导出补充：成功写入 ZIP 的每个附件在 manifest 中含流式计算的
SHA-256，可由接收方对归档内容做完整性核验；不改变来源身份字段、路径保密和大小限制。

P9/P11 QQ 内容补充：QCE `walletElement` 映射为仅含有界摘要的红包/钱包卡片，
不保留金额或 token 等原始字段；本地 QCE 语音缓存的 MCP 附件读取识别 SILK v3
(包括 `0x02` 前缀变体)、AMR-NB 与 AMR-WB 签名并返回对应音频内容。两者均以合成
数据验证，不访问远端头像、NapCat 或真实聊天数据。

P11 cached QQ audio now also recognizes ADTS AAC and ISO-BMFF audio brands
(`M4A`, `M4B`, `M4P`, `F4A`, `F4B`) using bounded signatures; generic `isom`
MP4 is not returned as voice content. Synthetic MCP tests cover both accepted
audio and rejected generic video signatures.

P11 WeChat `local_type=48` messages are normalized into offline location cards.
The Hub extracts bounded `location` XML attributes (place name, label, and
finite in-range coordinates), stores only those normalized fields in the Hub
index, and renders them as escaped text with no map link, remote tiles, or
network request. Malformed/empty payloads remain a generic location card;
coordinates are omitted unless valid. Synthetic parser/render tests cover
bounded XML, entity rejection, coordinate ranges, HTML escaping, and absence of
external links. Real-account schema/display acceptance remains outstanding.

**Report range parity (2026-10-12):** annual and pair reports now let users
choose all-time or a single year, matching the pinned WeFlow year selector.
All-time requests omit `year`, and report/CSV labels preserve the selected
range.

**MCP roster follow-up (2026-10-12):** `list_group_members` now reuses the
Hub's exact platform/account/conversation validation and local reader, with
bounded filtering/paging and the `chat` capability gate. It does not start a
sync; an unavailable reader fails closed. Synthetic tests cover scope, paging,
disabled capability, and the absent-reader path. This closes the MCP roster
entry-point gap, not real-account roster acceptance or full resource parity.

**QQ forwarded WMA attachment follow-up (2026-10-12):** nested forward
attachments now accept `audio/x-ms-wma` only when the bounded bytes match the
ASF Header Object GUID; the existing account-bound opaque handle serves both
explicit local download and MCP attachment reads. Invalid lookalike headers
remain unsupported. Synthetic provider, scoped Hub/MCP, and WebUI tests pass;
real cache availability and browser codec support are not verified. Full
resource-library and decryption parity remains partial.

P11 QQ media consumers mirror the reader's `extra.media` fallback when
`extra.roaming_resources` is absent or not an array; an explicit array remains
authoritative, including an empty one. Synthetic coverage spans chat/unified
media listings and analytics, indexed resolution/ZIP export, MCP selected
attachments, and referenced-media backup inclusion. A 272-test targeted backend
run and the latest five focused alias/precedence regressions pass. This closes
alias consistency only; full resource/decryption parity and real-cache
acceptance remain open.

P12 desktop notification settings also detect browsers without the Web
Notifications API, explain why enabling is unavailable, and preserve the off
action if the capability was enabled elsewhere. Synthetic settings-state,
permission, and click-target regressions pass; real browser permission and
notification-click acceptance remain outstanding.

2026-10-13 query/report parity correction: MCP `list_media` now supports exact
conversation-ID scoping and requires platform plus account whenever that ID is
provided; synthetic same-title conversations verify the distinction. Pair
reports include reply count, average reply time, and fastest reply time, covered
by analytics API and report export tests. These local checks do not close full
resource/decryption parity or real-device media acceptance.

**P11 daily self-send intensity (2026-10-13):** the statistics view now provides
a capability-gated, read-only cross-platform daily chart over private-chat
messages in the Hub index. The API computes an exact full-range daily baseline
in UTC+08 and returns zero-filled recent days; the UI plots relative daily
intensity and a 7-day moving average for the latest 31 days. The response
contains counts and dates only, and is bounded to 37 buckets. Synthetic
analytics, endpoint/capability, and WebUI rendering tests pass; real-account
display acceptance remains open.

**P11 WXGF/WXAM image decoding:** source-launched and portable Hub/MCP runtimes
resolve the repository's pinned `ffmpeg-static` binary for WeChat HEVC-backed
images. The portable bundle keeps the upstream license and build/source notices.
Decoded output stays capped at 64 MiB; conversion failure preserves the raw
`.wxgf` download. Synthetic conversion/fallback checks pass; real-account cache
availability and preview remain unverified.
Portable p332's isolated smoke executed its bundled FFmpeg, and sanitized
source p336 passed extracted startup/privacy checks; manifest p336 verifies
both artifacts against local-worktree provenance.

**Moments export follow-up (2026-10-14):** the filtered local Moments export now
also supports Excel/XLSX. It writes spreadsheet cells as escaped inline text,
omits remote media credentials, truncates cell content to Excel's supported
limit, and enforces the existing 32 MiB output cap while consuming paged rows.
Synthetic archive/XML, API, and WebUI tests pass. This closes the Moments XLSX
format gap only; recognized-schema coverage, real-account acceptance, and the
separate full resource-library/decryption parity gap remain open.

**Moments MCP image-format follow-up (2026-10-14):** the explicit cached-media
reader now verifies ISO-BMFF `ftyp` size and recognized AVIF/HEIC brands, so
those locally cached image formats can be returned as MCP image content. A
lookalike brand is rejected. Synthetic resolver and MCP tests pass; real cache
and client rendering acceptance remain open.

**QQ reply-preview media follow-up (2026-10-14):** bounded attachment metadata
embedded in local QQ kind-7 replies is now retained and can be resolved from
the account-scoped local cache inside the reply block. Synthetic checks pass;
real-account cache coverage remains part of the open P11 acceptance.

**P11 private analytics scope (2026-10-14):** private-profile reports now
exclude orphan messages without a recognized private conversation row, using
the same fail-closed scope rule as daily self-send analytics. Synthetic
coverage verifies that the explicit private profile remains intact.

**P13 WeChat live acceptance (2026-10-14):** one exact indexed conversation
was found, but the local readiness probe found no WeChat window. No message
request was queued or sent; the capability was returned to its default-off
state. Real delivery remains unverified.

**QQ reply attachment index regression (2026-10-14):** quoted resources are
filtered from the ordinary attachment strip without changing the indices used
by the local resolver. Synthetic rendering and full local checks pass; real
QQ cache acceptance remains open.

**WeChat send request (2026-10-14):** the user supplied the target and test
text, but the local Hub is unavailable and sending is disabled. No request was
created or sent. Resume from the Hub's normal readiness and approval flow.
