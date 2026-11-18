# 微信与企业微信主动下载实现方案：后台优先

研究日期：2026-10-28。目标是在中枢选择一条已有聊天记录，补下载其中尚未落盘的文件，并交给现有媒体预览、导出和 MCP 读取链路。

按“完全后台优先”的要求，应先寻找适配当前客户端的内部下载任务接口，并验证纯控件调用是否能够完成同一任务。wxauto 可参考消息识别与接收流程，但已核实的公开源码会操作系统鼠标、显示窗口并使用剪贴板，不能直接作为完全后台方案。企业微信官方网络接口可以后台下载，但机器人收件与授权存档的范围不同于员工电脑上的全部历史会话。

本轮只读检查到本机微信为 **4.1.15.13**、企业微信为 **5.0.13.6005**。目前尚未找到并验收一套同时适配这两个实际版本、可补下载既有会话缺失文件的完全后台实现。下文区分源码证据、条件可行的方向和仍待实测的能力。

## “完全后台”的验收口径

下载过程中不激活或置顶微信窗口，不移动系统鼠标，不发送全局键盘输入，不占用用户剪贴板，也不打开目标附件。把原有界面脚本放进后台线程或子进程，仍不能满足这一口径。

窗口被遮挡、最小化、Windows 锁屏和远程桌面断开分别验收，不能由其中一种状态推断其余状态。内部接口路线仍可能依赖已登录且存活的客户端进程；官方网络接口则可以脱离员工客户端运行。Windows 用户注销与锁屏也不是同一条件。

## 方案选择

| 路径 | 能覆盖什么 | 前提与限制 | 对中枢的选择 |
| --- | --- | --- | --- |
| 微信内部下载任务 / RPC | 理论上可按消息触发客户端接收，旧版项目有实现证据 | 当前 4.1.15.13 适配未找到并验收；旧版实现依赖进程注入 | 作为完全后台的重点研究方向，不能直接安装旧 SDK |
| 微信 / 企业微信纯 UIA 控件调用 | 控件确实暴露动作时，可能无需系统输入触发下载 | 当前两个版本的控件能力、窗口状态与完成条件均待验证 | 先做小范围能力验证，不能承诺改成 Invoke 就可后台 |
| 企业微信智能机器人 | 用户投递给机器人的新文件 | 机器人配置和消息回调；不包含机器人未参与的员工历史会话 | 真正的后台网络路线，独立来源 |
| 企业微信会话内容存档 | 授权存档范围内、接口仍允许拉取的消息与媒体 | 管理端开通、专用凭据、存档范围及相关配置 | 条件具备时优先采用，独立来源 |
| 客户端自动下载 + 缓存归档 | 客户端已经自动接收的新文件 | 取决于客户端设置与文件范围，不会主动补齐未缓存历史文件 | 可后台归档，不能算完成本任务 |
| wxauto 式鼠标与窗口操作 | 客户端中仍可接收且能定位的文件 | 占用交互桌面；已核实源码并非完全后台 | 仅作流程参考，不作为默认下载实现 |
| 临时素材或微盘接口 | 有合法素材标识或微盘权限的文件 | 标识、权限与聊天附件并不通用 | 不能用来补齐任意历史聊天文件 |

## wxauto 源码核查

用户指定项目为 [cluic/wxauto](https://github.com/cluic/wxauto)。本轮核查固定在公开 main 提交 `05e5a52379c99f401dc512acfdd811b3d701da8b`，下表的源码证据链接均指向该提交，避免未来代码变化混淆结论。

| 位置 | 已确认行为 | 对后台下载的影响 |
| --- | --- | --- |
| `wxauto.py` 初始化与 `_show()` | 标注版本 `3.9.11.17`，查找 `WeChatMainWndForPC`；构造时调用 `_show()`，其中 ShowWindow、置顶切换、SwitchToThisWindow | 不是本机 4.1 兼容性证明；构造对象也会影响窗口 |
| `elements.py` 的 `_download_file()` | 滚动目标消息进入视野，右键、点“复制”；必要时点文件和“接收文件”，再读 CF_HDROP 剪贴板路径并复制 | 下载链路仍依赖鼠标和用户剪贴板 |
| `uiautomation.py` 的 Click / RightClick | Control 方法最终调用全局点击；后者执行 SetCursorPos 与 mouse_event | `simulateMove=False` 只跳过平滑移动，不会消除鼠标输入 |
| `WeChatImage.Save()` | 保存对话框部分步骤使用 WM_SETTEXT 与 BM_CLICK | 只能说明这两个子步骤可按控件句柄操作，不能证明整条链路后台 |

证据：[版本与初始化](https://github.com/cluic/wxauto/blob/05e5a52379c99f401dc512acfdd811b3d701da8b/wxauto/wxauto.py#L22)、[显示窗口](https://github.com/cluic/wxauto/blob/05e5a52379c99f401dc512acfdd811b3d701da8b/wxauto/wxauto.py#L85)、[文件接收链路](https://github.com/cluic/wxauto/blob/05e5a52379c99f401dc512acfdd811b3d701da8b/wxauto/elements.py#L107)、[底层鼠标点击](https://github.com/cluic/wxauto/blob/05e5a52379c99f401dc512acfdd811b3d701da8b/wxauto/uiautomation.py#L1848)、[Control.Click](https://github.com/cluic/wxauto/blob/05e5a52379c99f401dc512acfdd811b3d701da8b/wxauto/uiautomation.py#L6129)、[保存对话框](https://github.com/cluic/wxauto/blob/05e5a52379c99f401dc512acfdd811b3d701da8b/wxauto/elements.py#L442)。

文件接收代码还有无整体超时的菜单重试循环；移植思路时不能直接继承它。其监听接口能轮询消息，不等于监听期间的文件接收无桌面操作。[重试与剪贴板读取](https://github.com/cluic/wxauto/blob/05e5a52379c99f401dc512acfdd811b3d701da8b/wxauto/elements.py#L133)、[监听实现](https://github.com/cluic/wxauto/blob/05e5a52379c99f401dc512acfdd811b3d701da8b/wxauto/wxauto.py#L580)。

作者当前在线文档涉及 wxauto4 / wxautox4 等产品，不能与上述 3.9 公开源码视为同一版本。`FileMessage.download(dir_path, force_click, timeout)` 标为 Plus，只能确认该产品文档提供下载入口；文档没有因此证明本机版本上可以最小化、锁屏、无鼠标地接收文件。消息 UI ID 随界面切换变化，也不能作为中枢持久消息主键。[作者消息文档](https://docs.wxauto.org/docs/class/Message.html)

当前安装文档列微信 4.1，但免费版明确只支持到 `4.1.8.107`；这低于本机 `4.1.15.13`，不能承诺安装免费版即可使用。作者还说明，4.x 只加载聊天窗口当前可视区的消息 UI，并会在部分监听流程调整窗口高度；这增加了纯 UIA 历史定位的限制，不能把“可视区”进一步推断为对所有窗口遮挡状态的结论。[作者当前文档：环境与 4.x 机制](https://docs.wxauto.org/llms-full.txt)

作者自己的 wxauto-mcp 项目也提示，部分操作要求微信窗口在前台可见、不要最小化。这是当前产品的一手限制说明，进一步排除了把“后台 API 服务”直接视作完全后台下载的判断；它仍未逐项证明主动文件下载在锁屏或远程桌面断开后可用。[作者 MCP 项目说明](https://github.com/cluic/wxauto-mcp/blob/main/README.md#3-微信窗口)

## 两条不抢桌面的客户端路线

### 内部下载任务：方向有源码依据，当前版本仍缺适配

[WeChatFerry](https://github.com/lich0821/WeChatFerry) 的 Python `download_attach(id, thumb, extra)` 发出 `FUNC_DOWNLOAD_ATTACH`。C++ 实现把消息 ID 解析为本地消息 ID 与数据库索引，取得消息对象，调用客户端 `GetPreDownLoadMgr` / `PushAttachTask`，没有通过点击文件卡片触发。这为“按消息请求客户端后台接收”提供了实际源码参考，而非仅有接口名称。[Python RPC](https://github.com/lich0821/WeChatFerry/blob/0f5c60a034fcac234cabd000b49c9200defa7f7d/clients/python/wcferry/client.py#L732)、[C++ 下载任务](https://github.com/lich0821/WeChatFerry/blob/0f5c60a034fcac234cabd000b49c9200defa7f7d/WeChatFerry/spy/misc_manager.cpp#L152)。

但它的适配记录为微信 `3.9.12.51`，仓库现已归档；初始化会向微信注入 `spy.dll`，内部函数也使用固定偏移。因此它不是当前微信 4.1.15.13 的即用方案，更不能套用于企业微信 5.0.13.6005。本轮未注入、安装、降级或运行该 SDK。[版本记录](https://github.com/lich0821/WeChatFerry/blob/0f5c60a034fcac234cabd000b49c9200defa7f7d/README.MD#L219)、[注入初始化](https://github.com/lich0821/WeChatFerry/blob/0f5c60a034fcac234cabd000b49c9200defa7f7d/WeChatFerry/sdk/sdk.cpp#L102)、[固定函数偏移](https://github.com/lich0821/WeChatFerry/blob/0f5c60a034fcac234cabd000b49c9200defa7f7d/WeChatFerry/spy/offsets.h#L143)。

内部任务返回值也不能当作完整文件的验收结果：该实现调用入队函数后返回，且遇到路径已存在会直接返回成功，代码自身留有按大小检查的 TODO。中枢必须另行等待完成并核验内容。源码未调用鼠标不等于本轮已经证明锁屏、最小化与断开远程桌面可用。

对微信下一步的有效证据应是：**明确适配 4.1.15.13 的附件接收入口、必要消息参数、可验证的完成状态**。对企业微信员工会话，尚未核实对应当前版本的公开内部下载实现；官方机器人 SDK 不能补上这个缺口。

### 纯控件调用：先验证控件能力，避免全局输入

wxauto 自带的 UIA 层已有 `GetInvokePattern()`、`GetValuePattern()` 等基础能力。可研究通过 Invoke、Value、Selection、Scroll 等控件模式完成消息定位、下载触发与保存，替代 SetFocus、SwitchToThisWindow、全局鼠标和剪贴板。微软文档说明这些能力由具体控件提供且可能随状态变化；Invoke 调用成功也只代表动作调用结果，不是文件下载完成。[wxauto Invoke 封装](https://github.com/cluic/wxauto/blob/05e5a52379c99f401dc512acfdd811b3d701da8b/wxauto/uiautomation.py#L3716)、[Microsoft 控件模式](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-controlpatternsoverview)、[Invoke API](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationinvokepattern-invoke)。

最小验证先只读检查两个实际客户端的会话、文件卡片及下载控件暴露哪些模式；只有它们确实提供必要能力，才尝试一条未缓存文件。不能直接构造旧版 wxauto 对象后把少数 Click 改成 Invoke，因为构造、滚动、菜单、保存和关闭仍可能抢焦点。纯控件调用也可能由客户端自身弹窗或激活窗口，必须记录实际结果。

如果某一步只能依赖坐标点击，后台模式应明确报告该后端不可用；不悄悄恢复窗口并操作用户桌面。另设虚拟机或独立交互会话可以隔离鼠标脚本，但仍依赖桌面环境，不作为完全后台能力的替代验收。

## 微信可以复用的部分

当前 `integrations/wechat/provider.py:14` 导入的是 `vendor.media`。`vendor/media.py:2077` 的 `download_file` 通过消息的 `server_id` 查文件名，再复制账号目录下 `msg/file/YYYY-MM/文件名` 的已有文件。这条路径没有发起网络接收，适合作为下载完成后的缓存读取基础。

同一文件的 `download_image_original` 从第 2153 行起包含驱动客户端、等待原图缓存并保存的流程，但当前不能直接启用：provider 第 142 行明确覆盖禁用；vendor 目录缺少其导入的 `uia_driver`、`guia`、`wx`。本机保留的上游 `uia_driver.py` 还包含 `WriteProcessMemory`。因此删除覆盖方法或复制整个上游驱动，都不是一个已经验证可用的最小补丁。[上游项目](https://github.com/fanyuantaier/wechatauto-replica)

wxauto 的消息类型识别、文件状态判断与超时参数可作为接口设计参考；其鼠标与剪贴板实现不直接复用。当前文档提醒自动下载可用时强制点击会打开文件，因此任何后端都应区分“接收”和“打开”。[wxauto 消息文档](https://docs.wxauto.org/docs/class/Message.html)

pywechat 的 `Files.save_chatfiles` 会跳过未下载、过期及发送中断项，再复制本机文件；它不能直接解决缺失附件补下载。[pywechat 文件导出源码](https://github.com/Hello-Mr-Crab/pywechat/blob/8589baa049bb91d3a500602c167f07b2f8397a13/src/pyweixin/WeChatAuto.py#L2464)

WeFlow 的文件导出实现可参考文件名、月份目录和 MD5 候选校验，但已核实的这段代码仍是本机文件查找与导出。其自动原图服务另需配置外部 DLL，并调用 `InitImgHelper`；这不足以提供一个可直接移植的独立下载实现。[WeFlow 文件导出](https://github.com/hicccc77/WeFlow/blob/fb58afa9876c6b334887a33471dd5f0077eedec4/electron/services/export/core/ExportContext.ts#L3893)、[原图 DLL 桥](https://github.com/hicccc77/WeFlow/blob/fb58afa9876c6b334887a33471dd5f0077eedec4/electron/services/imageDownloadService.ts#L36)

### 后台后端通过验证后的最小接入

增加独立的主动下载方法，保留现有 `media()` 缓存读取行为。普通预览、批量导出和 `get_attachment` 不隐式触发客户端下载；用户主动请求也必须遵守所选择后端已验证的后台能力。

1. 由中枢消息身份回查当前账号、会话、`shard`、`local_id` 和 `server_id`。`local_id` 会跨分片或会话重复，定位和缓存目录必须保留完整身份。
2. 内部接口按精确消息身份调用，不经会话显示名或坐标定位。UIA 路线必须用稳定会话 ID 关联显示名，结合文件名、发送方、时间及邻近消息与数据库记录比对；不能只凭名称打开同名会话。
3. 重复文件无法唯一定位时返回失败；不沿用原图代码中轮流尝试多个气泡的回退。后台模式不得调用发送模块的窗口激活与全局输入路径。
4. 发起客户端接收任务，或调用已经实测可用的下载控件模式。已下载时直接读取缓存，避免双击打开文档或程序。
5. 等待客户端完成，核对声明大小；能取到原始 MD5 时同时校验。仅看到文件存在或大小短暂停止增长，不能单独证明完整下载。
6. 沿用中枢现有单附件 32 MiB 产品上限，原子保存到账号与消息隔离的目录，再返回本地媒体地址。该上限是本项目选择，不是微信平台限制。

第一版只验证一个当前客户端版本的一条普通文件；原图与视频分别验收后再扩展。UIA 后端只接受其已验证的消息定位范围，不能把“当前可见消息可操作”宣传成任意历史文件可下载。未支持的客户端版本或窗口状态应返回明确结果。

## 企业微信的四套下载身份

### 现有员工会话

本机 `integrations/wecom/engine.py:488` 读取 `file_table4.message_id/md5`，再通过 `CacheMapping.mapping` 查已存在文件。第 514 行之后只有成功定位的缓存文件才形成相应媒体项。`hub/server.py:1297` 写入索引的附件信息主要是名称、类型和大小；这里没有官方存档 `sdkfileid` 的取得流程。

因此客户端方案首先要保留未下载文件的元数据，避免“没有缓存”同时变成“没有附件定位信息”。需要确认当前版本的消息副本能否提供名称、长度、摘要和资源序号，再寻找相应内部下载入口或可无输入调用的控件。不能把本地 MD5、缓存 key 或消息 ID 假设为官方下载凭据。

当前可复用的企业微信读取器是缓存解析器。微信 wxauto 的窗口类、控件与函数偏移均不能直接用于企业微信。员工会话后台下载仍需一个适配 5.0.13.6005 的验证原型；没有完成该验证前，不应把接口中的 `wecom` 平台标为可主动下载。

### 智能机器人收件

企业微信官方 Node SDK 已提供 `downloadFile(url, aesKey)`，Python SDK 对应 `await download_file(url, aes_key)`。消息回调带文件 URL 和解密参数，SDK 下载并还原文件；WebSocket 长连接可承担接收通道。[官方 Node SDK](https://github.com/WecomTeam/aibot-node-sdk#readme)、[官方 Python SDK](https://github.com/WecomTeam/wecom-aibot-python-sdk#readme)

源码确认文件解密使用 AES-256-CBC：Base64 解码密钥，以密钥前 16 字节作 IV，验证并去除按 32 字节倍数填充的 PKCS#7。文件名可从 HTTP `Content-Disposition` 读取。[解密源码](https://github.com/WecomTeam/aibot-node-sdk/blob/main/src/crypto.ts)、[下载源码](https://github.com/WecomTeam/aibot-node-sdk/blob/main/src/api.ts)

若增加此能力，接收器应按机器人身份与消息 ID 去重，保存文件定位信息并尽快完成下载、解密和原子落盘。回调 URL 的有效期、可回调聊天类型与大小应按接入模式核对；不能设计成保存一个 URL 后随时补下载。SDK 示例一次返回整个 Buffer，中枢仍需落实自身大小限制和失败处理。

机器人记录必须使用独立账号身份。它处理发给机器人的消息，不应冒充当前员工账号，也不能声称已补下载员工既有会话附件。

### 会话内容存档

官方提供 `GetChatData`、`DecryptData` 和 `GetMediaData` 路径：取得并解密授权存档消息，从文件消息取 `sdkfileid`，以 `indexbuf` 续取媒体数据，直到 SDK 报告完成。下载后可用消息中的长度和摘要校验。[官方 SDK 文档入口](https://developer.work.weixin.qq.com/document/path/91774)

这条路线要求管理端配置存档范围、可信 IP、消息加密公钥及专用凭据。它需要持续采集官方可拉取窗口内的数据，不能假设新开通后能回填员工电脑上的全部旧历史。[使用前帮助](https://developer.work.weixin.qq.com/document/path/91361)

此路线适合作为新的存档来源接入，而不是把现有本地缓存解析器直接改成远端下载器。当前具体套餐、拉取时限和调用频率需在实施时核对管理后台与对应 SDK 文档；本方案不据此承诺完整历史恢复。

### 临时素材与微盘

`GET /cgi-bin/media/get?access_token=...&media_id=...` 以合法临时素材 `media_id` 为输入，没有从任意员工、会话或时间反查聊天文件的参数。存档的 `sdkfileid` 不能交给该接口。[获取临时素材](https://developer.work.weixin.qq.com/document/path/90254)

微盘使用独立文件及空间权限。即使聊天内容引用微盘文档，也需要相应文件权限；不能把微盘文件标识与聊天附件标识互换。[微盘概述](https://developer.work.weixin.qq.com/document/path/93654)

## 接入中枢需要修改的位置

| 位置 | 必要改动 |
| --- | --- |
| `integrations/wechat/` | 后台后端验收后，新增按完整消息身份执行的文件下载方法，复用缓存读取与保存工具 |
| `integrations/wecom/engine.py` 与 `adapter.py` | 员工会话路线保留未下载附件元数据；加入经真实版本和后台状态验证的下载动作与缓存刷新 |
| `hub/server.py:1898` | 在 `download_attachment` 接入已可用平台，校验当前账号；复用现有模块开关与本地媒体返回 |
| `hub/mcp_server.py:309` | 仅对已可用后端扩展平台枚举与能力描述，说明运行所需的客户端及桌面状态 |
| `webui/app.js` | 仅对可支持的附件显示下载按钮，显示进行中、需登录、目标不明确、过期和完成状态 |
| 缓存读取与资源导出 | 新下载文件在重启后仍能由原消息解析；`get_attachment` 继续沿用 8 MiB 内容返回上限 |
| 测试与打包 | 覆盖身份绑定、取消、下载中断、摘要错误和原子发布；包含实际使用的后端依赖，不整体引入旧版驱动 |

同一客户端后端按其实际并发能力调度。若 UIA 下载会更改当前会话，应与其他客户端操作串行，且验收其对用户正在操作的影响。机器人和存档网络下载不需要占用桌面操作锁。

## 实施顺序与验收

先核实当前版本内部下载入口，同时做范围有限的纯 UIA 能力检查；两者均不满足时，报告缺口，不先接入鼠标脚本充当后台功能。微信与企业微信分别验证，不能以一端成功代替另一端。机器人收件与授权存档按各自前提独立接入，不替代既有员工会话验收。

一次端到端验收必须满足：下载前目标文件确实不在本机缓存；请求绑定的账号、会话、消息与附件唯一；客户端实际接收完成；中枢保存的字节与声明大小及可用摘要一致；重启后仍可通过原消息读取、导出。任务入队返回成功不算通过。

后台验收同时记录前台窗口、光标与剪贴板是否受影响，并分别测试遮挡、最小化、锁屏、远程桌面断开。锁屏等状态未通过时，只能陈述已通过的条件，不能标为完全后台。过期、重名、账号切换和下载中断要返回可解释结果，不能误报成功或打开附件。

本轮仅更新研究文档并只读查看客户端版本。真实客户端控件模式、未缓存文件下载、各桌面状态及企业微信管理端权限均未验收；没有修改客户端设置、安装或注入 SDK、降级客户端、发送测试消息或改动应用代码及运行实例。

## 2026-10-29 实施结果

微信普通文件主动下载已接入中枢。实现没有采用旧版注入 SDK，也没有把 CDN 元数据直接当作可离线下载凭据；当前有效路线是：从统一索引恢复账号、会话、`shard`、`local_id`、文件名、声明大小、MD5 和过期时间，先校验账号及消息身份，再读取微信账号缓存。微信 4.1 的 `message_resource.packed_info` 现在按 protobuf 解码，修复了把嵌套字段拼成错误文件名的问题，并支持微信生成的 `(1)`、`(2)` 重名缓存。

缓存缺失时，中枢调用已登录微信 4.1.x 客户端的文件上下文菜单“下载”。该动作不打开附件、不写系统剪贴板，但依赖交互桌面，可能显示/置前微信、移动鼠标、切换会话和滚动历史；因此只标注为“主动下载”，不宣称完全后台。下载完成后，中枢等待文件大小稳定，按消息声明的大小和 MD5 校验，再以分片写入、`fsync` 和原子替换发布到账号与消息隔离的 Hub 目录。过期、超过 32 MiB、类型不符、定位失败、超时或摘要不一致都会失败关闭。

2026-10-29 的客户端动作验收使用微信 4.1.15.13 中一条下载前不在缓存内的 27,163,842 字节文件：右键“下载”后客户端实际完成接收，验收脚本从账号缓存读到完整文件，MD5 为 `2cb74874274843aca5d862def352d9e1`，与消息声明一致。另一次真实 HTTP 验收将已缓存的 81,908 字节文件原子导入中枢，流式读回的大小与 MD5（`39aa785817a7a4fe3c78b42b5f0197ab`）一致。随后同一账号在另一台设备登录，Windows 客户端退出登录，导致“生产模块从缓存缺失开始触发客户端并导入”的再次验收被阻断；因此锁屏、最小化、远程桌面断开以及退出登录后的行为仍未通过，也未据此扩大后台能力声明。企业微信员工历史会话主动下载仍未接入。
