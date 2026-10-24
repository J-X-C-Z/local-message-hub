# 中山大学校园信息来源

目录与公开列表于 **2026-10-23** 核验。此能力提供官方公开信息的首屏聚合和服务入口，不表示已经取得全校全部历史文章、登录后通知、个人课表或成绩。

## 目录范围

`integrations/campus/catalog.py` 包含 95 个来源：16 个可采集公开列表，9 个服务/目录/动态网页入口，以及从[学校官方院系目录](https://www.sysu.edu.cn/yxsz.htm)提取的 70 个院系网站入口。院系数量是该目录中的网站条目数，不是对现行独立学院数量的判断；院系站点不逐个爬取。机构网址参照[学校服务机构目录](https://www.sysu.edu.cn/xxg/fwjg.htm)。

面向电子与信息学院本科生，特别列出学院本科教务、学生通知、学生活动三个来源，覆盖选课、竞赛、奖学金的公开通知。来源若只属于某校区，其 scope 记录该校区；全校来源的 campus 留空，不据标题猜测全部适用校园。

## 实际采集验证

以下为本机通过 HTTPS 抓取并解析列表页的结果，均返回 `ok`。仅取当前页面，每个来源最多 100 条，未扫描历史分页、下载文章附件或展开二级网页。条数可以重复（学校官网与新闻网），中枢按规范化文章 URL 去重。

| 来源 | 官方地址 | 抽取条数 | 有完整发布日期 |
|---|---|---:|---:|
| 学校官网 | https://www.sysu.edu.cn/ | 14 | 13 |
| 中大新闻网 | https://www.sysu.edu.cn/news/ | 48 | 22 |
| 教务部通知 | https://jwb.sysu.edu.cn/newscenter/notice | 20 | 20 |
| 学院本科教务 | https://seit.sysu.edu.cn/undergraduate/inform | 20 | 20 |
| 学院学生通知/奖助 | https://seit.sysu.edu.cn/student/notice | 20 | 20 |
| 学院学生活动 | https://seit.sysu.edu.cn/student/activities | 17 | 17 |
| 学生工作部 | https://xgb.sysu.edu.cn/ | 12 | 6 |
| 校团委 | https://zdtw.sysu.edu.cn/ | 21 | 21 |
| 研究生院 | https://graduate.sysu.edu.cn/ | 12 | 12 |
| 图书馆公告 | https://library.sysu.edu.cn/news | 21 | 20 |
| 就业信息网 | https://career.sysu.edu.cn/ | 100 | 18 |
| 国际合作交流 | https://eao.sysu.edu.cn/ | 14 | 12 |
| 总务部 | https://zwc.sysu.edu.cn/ | 40 | 40 |
| 保卫处 | https://bwc.sysu.edu.cn/ | 15 | 1 |
| 珠海校区 | https://fgw.sysu.edu.cn/zhuhai | 17 | 12 |
| 深圳校区 | https://shenzhen.sysu.edu.cn/ | 13 | 12 |

共抽取 **404 条（去重前）**。名单页第一条不一定最新；如校团委首屏存在 2023 年置顶文章，保留原始发布日期，不能将采集时间当作发布日期。无完整年份的时间不会补今年，未能解析日期留空。

网络中心 https://inc.sysu.edu.cn/news 与心理健康中心 https://psycorner.sysu.edu.cn/ 本次返回动态页面外壳，没有可提取列表，故作为入口呈现。校历 https://www.sysu.edu.cn/index/xl.htm、本科教务系统 https://jwxt.sysu.edu.cn/jwxt/、大学服务中心 https://usc.sysu.edu.cn/、统一门户 https://portal.sysu.edu.cn/ 保留入口，由用户自行打开或登录，中枢不接触登录凭证。全校院系和服务机构目录同样只作入口。

## 微信公众号文章

### 本机已关注公众号

「本机公众号」入口从本机联系人索引中查找中山大学、中大学工、康乐解学等相关公众号，仅允许选择经确认的 `official` 联系人。按当前微信账号与精确公众号 ID，从已缓存密钥可读的 `biz_message_*.db` 中读取对应表；不扫描私人会话、不提取新密钥、不访问公众号远程历史接口。

2026-10-23 本机验收：找到9个相关账号，归档259条文章卡片：中山大学32、康乐解学4、学院30、教务部44、中大学工73、团委35、学生社团在线26、信息技术帮助台15；设备处没有可归档文章。多篇推送卡片会拆成多条文章，所以文章数可大于消息数。

缓存卡片包括标题、摘要、原文链接，标记 `content_status=summary_only`，不表示完整正文。明确的 XML 发布时间才写入 published_at；接收时间另存 received_at，不将接收日期冒充发布日期。重复导入按去除跟踪参数后的文章链接去重，收藏、已读和已获取全文均保留。

正式服务默认每15分钟检查公开来源及已选择的本机公众号，逐公众号、逐数据库分片保存时间与消息ID游标，分页处理积压；只有完成整页写入才推进游标。微信新推送必须先到达本机微信缓存。每轮最多尝试20篇待获取全文，失败后冷却6小时，手动“获取全文/重试”可即时重试。网页“自动更新”可暂停或调整间隔、正文开关和批次；配置与检查时间持久化。`--no-autosync`、单独MCP进程及只读查询不启动定时采集。

`GET/POST /api/campus/updates` 读取/修改配置；`POST /api/campus/items/{id}/fetch` 获取正文。只有真实正文才标记 `full_text`，失败保留摘要并标记 `fetch_error`。2026-10-23实测中山大学、康乐解学、学院各一篇均被微信访问验证拦截，未取得真实全文；内置浏览器复核康乐解学同样跳转验证页。本机网页缓存未命中这259篇文章，不能将解析器测试当作真实全文验收。

数据写入同一存储目录的校园索引，MCP既有校园搜索可查询它们；原微信数据库和公众号关注状态均不修改。

官方列表中指向 `https://mp.weixin.qq.com/s/...` 的链接可作为有原始来源的索引条目保留。用户也可提供单篇公开文章链接；`fetch_wechat_article(url)` 尝试提取标题、公众号名称、正文和可识别的发布时间。不枚举公众号历史，不使用账号 Cookie，不处理登录或验证码，不自动打开其中的资源。

本次尝试学校官网链接的[公开公众号文章](https://mp.weixin.qq.com/s/36_aRrjq-jXDvAG9Y4Lx0g)未成功获取正文，返回安全错误，**不能据此声称微信真实正文导入已验收**。可读 HTML 的正文提取、异常页面和 URL 边界由离线测试覆盖。该文章失败不影响官网链接索引及其他官方来源。

## 采集与接口约定

所有固定来源必须使用 HTTPS 且属于 `sysu.edu.cn` 或子域；网络重定向逐次检查域名和协议。单篇微信仅允许 `mp.weixin.qq.com` 的 `/s` 或 `/s/...`，不允许其他页面、账号历史接口或跨域重定向。启用系统 TLS 证书验证，超时 12 秒，单页上限 2 MiB，输出最多 100 条。每个来源独立返回 `ok`、`empty`、`error` 或 `portal`；错误不附带服务端正文、凭证或网络异常详情。

`SOURCES` 每项包含 id/name/url/category/category_name/kind/scope/enabled。`kind=feed` 可采集，`portal/directory` 不联网只返回入口。`CATEGORIES` 为分类键到中文名称的映射。

`parse_source(html, source)` 返回文章数组；`fetch_source(source)` 与 `fetch_wechat_article(url)` 返回 `{items, status, error}`。文章包括稳定 URL 哈希 id、url、title、published_at（YYYY-MM-DD 或空串）、summary、category、source_id、source_name、campus；学院来源额外提供 faculty。列表摘要不臆造，留空；单篇微信可包含纯文本 content。

离线验证：`python -m unittest integrations.campus.test_adapter hub.test_campus_updates integrations.wechat.test_official_accounts hub.test_campus_integration -v`，覆盖正文解析与访问验证识别、解压上限、增量分页、重启续读、失败重试、状态保留和HTTP/MCP接口。
