"""Resolve a local QQNT peer identity into a OneBot 11 send target.

The local message tables key private conversations by QQNT ``uid`` (for
example ``u_-PBswiplK...``) while OneBot 11 actions accept only numeric
uin/group code. NapCat resolves that difference itself: a group uses
``group_id`` verbatim as ``peerUid`` (chatType 2), and a private chat first
converts ``user_id`` into a uid before checking friendship
(``napcat.mjs`` ``F2``/``Ty.base_handle``). This module performs the equivalent
mapping from the reader's own snapshot so a locally opened conversation can be
prepared for delivery without guessing and without any online lookup.

Unknown or ambiguous identities are refused instead of guessed: a prepared
send request must never target the wrong chat.
"""
import re


_UIN_RE = re.compile(r'^\d{5,20}$')


def is_uin(value):
    """True when the value already is a OneBot-compatible numeric identifier."""
    return bool(_UIN_RE.fullmatch(value or ''))


class SendTargetError(Exception):
    """Raised when no unambiguous OneBot target exists for a local identity."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def resolve_send_target(identity, kind, uins=None):
    """Return ``{"conversation_id": <digits>}`` for a local peer.

    ``uins`` maps local private uids to numeric uins and comes from the reader's
    own uid-mapping/profile tables. A group is expected to already carry its
    group code, because OneBot takes ``group_id`` verbatim as the group peer.
    """
    identity = identity if isinstance(identity, str) else ''
    kind = kind if isinstance(kind, str) else 'private'
    if kind not in ('private', 'group'):
        raise SendTargetError('send_target_kind_unsupported', '不支持的 QQ 会话类型')
    if not identity:
        raise SendTargetError('send_target_missing', '缺少 QQ 会话标识')
    if is_uin(identity):
        return {'conversation_id': identity}
    if kind == 'group':
        raise SendTargetError('send_target_group_unresolved',
                              '该群只有本地 UID，未找到对应群号；请先在本机 QQ 中打开该群后再试')
    mapped = (uins if isinstance(uins, dict) else {}).get(identity)
    if is_uin(mapped if isinstance(mapped, str) else ''):
        return {'conversation_id': mapped}
    raise SendTargetError('send_target_private_unresolved',
                          '该联系人只有本地 UID，未找到对应 QQ 号；请先在本机 QQ 中与其通讯后再试')
