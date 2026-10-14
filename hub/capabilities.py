"""Persisted feature availability and permission checks for the local Hub."""

CAPABILITIES = {
    "search": {"name": "全局搜索", "available": True, "core": True, "default": True, "reason": ""},
    "chat": {"name": "聊天记录", "available": True, "default": True, "reason": ""},
    "contacts": {"name": "联系人", "available": True, "default": True, "reason": ""},
    "media": {"name": "媒体库", "available": True, "default": True, "reason": ""},
    "stats": {"name": "基础统计", "available": True, "default": True, "reason": ""},
    "imports": {"name": "文件导入", "available": True, "default": True, "reason": ""},
    "sync": {"name": "本机同步", "available": True, "default": True, "reason": ""},
    "export": {"name": "聊天导出", "available": True, "default": True, "reason": ""},
    "organization": {"name": "本地整理", "available": True, "default": True, "reason": ""},
    "qq_history": {"name": "QQ 在线漫游历史", "available": False, "default": False,
                   "reason": "QQ 已改为只读本地数据库；本机历史通过同步读取，不再使用扫码或在线漫游。"},
    "mcp": {"name": "MCP 服务", "available": True, "default": True, "reason": ""},
    "advanced_analytics": {"name": "高级统计", "available": True, "default": True, "reason": ""},
    "annual_reports": {"name": "年度与双人报告", "available": True, "default": True, "reason": ""},
    "qq_send": {"name": "QQ 文本发送", "available": True, "default": False,
                "reason": "需要已有在线 NapCat 通道；本地数据库读取不能发送。发送目标从本地会话解析为数字 QQ 号 / 群号，本机逐条审批，MCP 不能直接投递。"},
    "wecom_bot_send": {"name": "企业微信机器人消息发送", "available": True, "default": False,
                       "reason": "使用独立机器人身份，不代表当前员工；仅在本机逐条审批后发送。"},
    "moments": {"name": "朋友圈", "available": True, "default": False,
                "reason": "仅读取本机已解密的 sns.db；不访问远程媒体服务。"},
    "voice_transcription": {"name": "本机语音转写", "available": True, "default": False,
                            "reason": "仅使用本机缓存语音与可选本地模型；默认关闭，不会下载模型。"},
    "notifications": {"name": "桌面通知", "available": True, "default": False,
                      "reason": "默认关闭；启用后仅通过本机事件流推送新消息元数据。"},
    "auto_export": {"name": "自动导出", "available": True, "default": False,
                    "reason": "用户配置后在本机生成定期导出文件；不上传数据。"},
    "relationship_footprint": {"name": "关系足迹", "available": True, "default": False,
                               "reason": "仅汇总本地消息频次与 UTC+08 日期，不返回正文或推断关系。"},
    "account_safety": {"name": "账号安全与应用锁", "available": True, "default": False,
                       "reason": "本机 WebUI 口令锁；独立 MCP stdio 客户端不受此锁控制。"},
    "anti_recall": {"name": "防撤回拦截", "available": False, "default": False,
                    "reason": "需要修改或拦截客户端数据库行为，尚未完成独立风险评估。"},
    "wechat_send": {"name": "个人微信文本发送", "available": True, "default": False,
                    "reason": "通过 GUI 拟人操作本机已登录的微信客户端真实发送；依赖本机微信窗口、"
                              "输入焦点与会话标题可读，风控风险由使用者承担；每条消息仍需本机审批，"
                              "MCP 不能直接投递。"},
}


def normalize_capabilities(value=None):
    """Ignore unknown or unavailable IDs and keep core capabilities enabled."""
    value = value if isinstance(value, dict) else {}
    return {
        name: (True if details.get("core") else
               value.get(name) if isinstance(value.get(name), bool) else details["default"])
        if details["available"] else False
        for name, details in CAPABILITIES.items()
    }


def describe_capabilities(value=None):
    enabled = normalize_capabilities(value)
    return {
        name: {**details, "enabled": enabled[name]}
        for name, details in CAPABILITIES.items()
    }


def unavailable_reason(name):
    details = CAPABILITIES.get(name)
    if details is None:
        return "未知功能模块"
    if not details["available"]:
        return details["reason"]
    return "此功能模块已在设置中关闭"
