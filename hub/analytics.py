"""Read-only analytics and report models over the unified chat index."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
try:
    from .store import media_message_predicate, parse_bound
except ImportError:
    from store import media_message_predicate, parse_bound


def _has_attachment_type(alias, kinds):
    values = ",".join("'" + kind + "'" for kind in kinds)
    attachments = f"""CASE WHEN json_valid({alias}.extra) THEN CASE
        WHEN {alias}.platform='wecom' AND json_type({alias}.extra,'$.attachments')='array'
            THEN json_extract({alias}.extra,'$.attachments')
        WHEN {alias}.platform='qq' AND json_type({alias}.extra,'$.roaming_resources')='array'
            THEN json_extract({alias}.extra,'$.roaming_resources')
        WHEN {alias}.platform='qq' AND json_type({alias}.extra,'$.media')='array'
            THEN json_extract({alias}.extra,'$.media')
        ELSE '[]' END ELSE '[]' END"""
    return f"""({alias}.platform IN ('wecom','qq') AND EXISTS (
        SELECT 1 FROM json_each({attachments}) AS resource
        WHERE lower(COALESCE(NULLIF(json_extract(resource.value,'$.type'),''),
                             NULLIF(json_extract(resource.value,'$.kind'),''),
                             json_extract(resource.value,'$.k'),'file')) IN ({values})
    ))"""


@dataclass(frozen=True)
class ConversationProfile:
    platform: str
    account_id: str
    conversation_id: str
    title: str
    kind: str
    message_count: int
    sent_count: int
    received_count: int
    sent_ratio: float
    last_timestamp: int | None
    first_timestamp: int | None
    media_count: int

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class MemberProfile:
    sender_id: str
    name: str
    message_count: int
    sent_count: int
    received_count: int
    first_timestamp: int | None
    last_timestamp: int | None

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class AnnualReport:
    platform: str
    account_id: str
    year: int | None
    conversation_id: str | None
    total_messages: int
    sent_messages: int
    received_messages: int
    sent_ratio: float
    active_days: int
    first_timestamp: int | None
    last_timestamp: int | None
    monthly: tuple[dict, ...]
    hourly: tuple[dict, ...]
    message_types: tuple[dict, ...]
    busiest_day: dict | None
    busiest_hour: dict | None
    activity_heatmap: tuple[tuple[int, ...], ...]
    longest_streak: dict | None
    top_contacts: tuple[dict, ...]

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class PairReport:
    platform: str
    account_id: str
    conversation_id: str
    conversation_title: str
    year: int | None
    total_messages: int
    sent_messages: int
    received_messages: int
    sent_ratio: float
    counterpart_id: str | None
    counterpart_name: str
    counterpart_messages: int
    response_count: int
    response_avg_seconds: int | None
    response_fastest_seconds: int | None
    active_days: int
    first_timestamp: int | None
    last_timestamp: int | None
    hourly: tuple[dict, ...]
    message_types: tuple[dict, ...]

    def to_dict(self):
        return asdict(self)


class AnalyticsService:
    """Analytics derived exclusively from the Hub's normalized SQLite store."""

    PRIVATE_CONVERSATION_KINDS = ("private", "direct", "friend", "single", "personal", "dm")

    def __init__(self, store):
        self.store = store

    def self_sent_daily_distribution(self, platform="all", account_id=None, start=None, end=None):
        """Return recent UTC+08 self-send days against the full-range daily baseline."""
        lower, upper = self._bounds(start, end)
        scope, params = self._scope(platform, account_id)
        bounds, date_params = self._date_predicate(lower, upper)
        kinds = ",".join("?" for _ in self.PRIVATE_CONVERSATION_KINDS)
        where = f"{scope}{bounds} AND m.is_self=1 AND LOWER(c.kind) IN ({kinds})"
        query_params = params + date_params + list(self.PRIVATE_CONVERSATION_KINDS)
        with self.store._lock:
            conn = self.store._require_connection()
            summary = conn.execute(
                f"""SELECT COUNT(*) AS total,
                           MIN(date(m.timestamp,'unixepoch','+8 hours')) AS first_day,
                           MAX(date(m.timestamp,'unixepoch','+8 hours')) AS last_day
                    FROM messages m JOIN conversations c
                      ON c.platform=m.platform AND c.account_id=m.account_id AND c.id=m.conversation_id
                    WHERE {where}""", query_params
            ).fetchone()
            total = int(summary["total"] or 0)
            if not total:
                return {"platform": platform, "account_id": account_id, "total_messages": 0,
                        "first_day": None, "last_day": None, "baseline": 0.0, "items": []}
            first_day = date.fromisoformat(summary["first_day"])
            last_day = date.fromisoformat(summary["last_day"])
            first_visible_day = max(first_day, last_day - timedelta(days=36))
            daily = conn.execute(
                f"""SELECT date(m.timestamp,'unixepoch','+8 hours') AS day, COUNT(*) AS count
                    FROM messages m JOIN conversations c
                      ON c.platform=m.platform AND c.account_id=m.account_id AND c.id=m.conversation_id
                    WHERE {where}
                      AND date(m.timestamp,'unixepoch','+8 hours') >= ?
                    GROUP BY day ORDER BY day""",
                query_params + [first_visible_day.isoformat()],
            ).fetchall()
        counts = {row["day"]: int(row["count"]) for row in daily}
        items = []
        day = first_visible_day
        while day <= last_day:
            key = day.isoformat()
            items.append({"date": key, "count": counts.get(key, 0)})
            day += timedelta(days=1)
        day_count = (last_day - first_day).days + 1
        return {"platform": platform, "account_id": account_id, "total_messages": total,
                "first_day": first_day.isoformat(), "last_day": last_day.isoformat(),
                "baseline": total / day_count, "items": items}

    @staticmethod
    def _scope(platform="all", account_id=None, conversation_id=None):
        where = []
        params = []
        if platform and platform != "all":
            where.append("m.platform = ?")
            params.append(str(platform))
        if account_id is not None:
            where.append("m.account_id = ?")
            params.append(str(account_id))
        if conversation_id is not None:
            where.append("m.conversation_id = ?")
            params.append(str(conversation_id))
        return (" AND ".join(where) if where else "1=1"), params

    @staticmethod
    def _bounds(start=None, end=None):
        lower = parse_bound(start) if start not in (None, "") else None
        upper = parse_bound(end, end=True) if end not in (None, "") else None
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("start must not be after end")
        return lower, upper

    @staticmethod
    def _date_predicate(lower, upper, alias="m"):
        clauses, params = [], []
        if lower is not None:
            clauses.append(f"{alias}.timestamp >= ?")
            params.append(lower)
        if upper is not None:
            clauses.append(f"{alias}.timestamp <= ?")
            params.append(upper)
        return (" AND " + " AND ".join(clauses) if clauses else ""), params

    def private_profiles(self, platform="all", account_id=None, start=None, end=None, limit=500, offset=0):
        """Return one message-derived profile per private conversation."""
        lower, upper = self._bounds(start, end)
        scope, params = self._scope(platform, account_id)
        bounds, date_params = self._date_predicate(lower, upper)
        params += date_params
        limit = max(0, min(int(limit), 5000))
        offset = max(0, int(offset))
        with self.store._lock:
            conn = self.store._require_connection()
            rows = conn.execute(
                f"""
                SELECT m.platform, m.account_id, m.conversation_id,
                       COALESCE(c.title, m.conversation_id) AS title,
                       COALESCE(c.kind, 'private') AS kind,
                       COUNT(*) AS message_count,
                       SUM(CASE WHEN m.is_self = 1 THEN 1 ELSE 0 END) AS sent_count,
                       SUM(CASE WHEN m.is_self = 0 THEN 1 ELSE 0 END) AS received_count,
                       MIN(m.timestamp) AS first_timestamp, MAX(m.timestamp) AS last_timestamp,
                       SUM(CASE WHEN {media_message_predicate('m')} THEN 1 ELSE 0 END) AS media_count
                FROM messages m LEFT JOIN conversations c
                  ON c.platform=m.platform AND c.account_id=m.account_id AND c.id=m.conversation_id
                WHERE {scope}{bounds}
                  AND LOWER(COALESCE(c.kind, '')) IN ({','.join('?' for _ in self.PRIVATE_CONVERSATION_KINDS)})
                GROUP BY m.platform,m.account_id,m.conversation_id
                ORDER BY message_count DESC, last_timestamp DESC, title COLLATE NOCASE
                LIMIT ? OFFSET ?
                """, params + list(self.PRIVATE_CONVERSATION_KINDS) + [limit, offset]
            ).fetchall()
        return [ConversationProfile(
            row["platform"], row["account_id"], row["conversation_id"], row["title"], row["kind"],
                int(row["message_count"]), int(row["sent_count"] or 0), int(row["received_count"] or 0),
                round((row["sent_count"] or 0) / row["message_count"], 6),
            row["last_timestamp"], row["first_timestamp"], int(row["media_count"] or 0),
        ) for row in rows]

    def group_profiles(self, platform="all", account_id=None, start=None, end=None, limit=500, offset=0,
                       member_limit=None, conversation_id=None):
        """Return group conversation aggregates with sender-derived member profiles."""
        lower, upper = self._bounds(start, end)
        scope, params = self._scope(platform, account_id)
        bounds, date_params = self._date_predicate(lower, upper)
        params += date_params
        conversation_filter = " AND m.conversation_id=?" if conversation_id else ""
        limit = max(0, min(int(limit), 5000))
        offset = max(0, int(offset))
        if member_limit is not None:
            member_limit = max(0, min(int(member_limit), 5000))
        with self.store._lock:
            conn = self.store._require_connection()
            groups = conn.execute(
                f"""
                SELECT m.platform,m.account_id,m.conversation_id,
                       COALESCE(c.title,m.conversation_id) AS title,
                       c.member_count AS roster_member_count, COUNT(*) AS message_count,
                       SUM(CASE WHEN m.is_self=1 THEN 1 ELSE 0 END) AS sent_count,
                       MIN(m.timestamp) AS first_timestamp,MAX(m.timestamp) AS last_timestamp
                FROM messages m LEFT JOIN conversations c
                  ON c.platform=m.platform AND c.account_id=m.account_id AND c.id=m.conversation_id
                WHERE {scope}{bounds}{conversation_filter} AND LOWER(COALESCE(c.kind,''))='group'
                GROUP BY m.platform,m.account_id,m.conversation_id
                ORDER BY message_count DESC,last_timestamp DESC,title COLLATE NOCASE
                LIMIT ? OFFSET ?
                """, params + ([conversation_id] if conversation_id else []) + [limit, offset]
            ).fetchall()
            member_rows_by_group = {}
            if groups:
                conversation_ids = list(dict.fromkeys(row["conversation_id"] for row in groups))
                placeholders = ",".join("?" for _ in conversation_ids)
                member_rows = conn.execute(
                    f"""
                    WITH ranked_members AS (
                        SELECT m.platform,m.account_id,m.conversation_id,
                               COALESCE(NULLIF(m.sender_id,''),'unknown') AS sender_id,
                               COALESCE(NULLIF(MAX(c.name),''),NULLIF(MAX(m.sender_name),''),
                                        NULLIF(m.sender_id,''),'未知成员') AS name,
                               COUNT(*) AS message_count,
                               SUM(CASE WHEN m.is_self=1 THEN 1 ELSE 0 END) AS sent_count,
                               MIN(m.timestamp) AS first_timestamp,MAX(m.timestamp) AS last_timestamp,
                               COUNT(*) OVER (PARTITION BY m.platform,m.account_id,m.conversation_id)
                                   AS observed_member_count,
                               ROW_NUMBER() OVER (
                                   PARTITION BY m.platform,m.account_id,m.conversation_id
                                   ORDER BY COUNT(*) DESC,
                                       COALESCE(NULLIF(MAX(c.name),''),NULLIF(MAX(m.sender_name),''),
                                                NULLIF(m.sender_id,''),'未知成员') COLLATE NOCASE,
                                       COALESCE(NULLIF(m.sender_id,''),'unknown')
                               ) AS member_rank
                        FROM messages m LEFT JOIN contacts c
                          ON c.platform=m.platform AND c.account_id=m.account_id AND c.id=m.sender_id
                        WHERE {scope}{bounds} AND m.conversation_id IN ({placeholders})
                        GROUP BY m.platform,m.account_id,m.conversation_id,
                                 COALESCE(NULLIF(m.sender_id,''),'unknown')
                    )
                    SELECT * FROM ranked_members
                    WHERE ? IS NULL OR member_rank <= ? OR (? = 0 AND member_rank = 1)
                    ORDER BY platform,account_id,conversation_id,member_rank
                    """, params + conversation_ids + [member_limit, member_limit, member_limit]
                ).fetchall()
                for row in member_rows:
                    key = (row["platform"], row["account_id"], row["conversation_id"])
                    member_rows_by_group.setdefault(key, []).append(row)
            result = []
            for group in groups:
                member_rows = member_rows_by_group.get(
                    (group["platform"], group["account_id"], group["conversation_id"]), []
                )
                displayed_member_rows = member_rows if member_limit is None else member_rows[:member_limit]
                members = [MemberProfile(
                    row["sender_id"], row["name"], int(row["message_count"]), int(row["sent_count"] or 0),
                    int(row["message_count"] - (row["sent_count"] or 0)), row["first_timestamp"], row["last_timestamp"]
                ).to_dict() for row in displayed_member_rows]
                observed_member_count = int(member_rows[0]["observed_member_count"]) if member_rows else 0
                result.append({
                    "platform": group["platform"], "account_id": group["account_id"],
                    "conversation_id": group["conversation_id"], "title": group["title"],
                    "message_count": int(group["message_count"]),
                    "sent_count": int(group["sent_count"] or 0),
                    "received_count": int(group["message_count"] - (group["sent_count"] or 0)),
                    "sent_ratio": round((group["sent_count"] or 0) / group["message_count"], 6),
                    "first_timestamp": group["first_timestamp"], "last_timestamp": group["last_timestamp"],
                    "member_count": int(group["roster_member_count"] if group["roster_member_count"] is not None
                                         else observed_member_count),
                    "observed_member_count": observed_member_count,
                    "members": members,
                })
        return result

    def annual_report(self, year, platform="all", account_id=None, conversation_id=None):
        lower = upper = None
        if year is not None:
            year = int(year)
            if year < 1970 or year > 9999:
                raise ValueError("year must be between 1970 and 9999")
            # Convert calendar bounds with the store's UTC+08 date semantics.
            lower, upper = self._bounds(f"{year:04d}-01-01", f"{year:04d}-12-31")
        return self._period_report(year, platform, account_id, conversation_id, lower, upper)

    def pair_report(self, platform, account_id, conversation_id, year=None, counterpart_id=None):
        lower = upper = None
        if year is not None:
            year = int(year)
            if year < 1970 or year > 9999:
                raise ValueError("year must be between 1970 and 9999")
            lower, upper = self._bounds(f"{year:04d}-01-01", f"{year:04d}-12-31")
        with self.store._lock:
            conn = self.store._require_connection()
            conv = conn.execute(
                "SELECT COALESCE(title,id) AS title,COALESCE(kind,'private') AS kind "
                "FROM conversations WHERE platform=? AND account_id=? AND id=?",
                (str(platform), str(account_id), str(conversation_id)),
            ).fetchone()
            if not conv:
                raise KeyError("conversation not found")
            if str(conv["kind"]).lower() not in self.PRIVATE_CONVERSATION_KINDS:
                raise ValueError("pair reports are available only for private conversations")
            scope = "platform=? AND account_id=? AND conversation_id=?"
            values = [str(platform), str(account_id), str(conversation_id)]
            time_sql, time_values = self._date_predicate(lower, upper)
            if counterpart_id is not None:
                scope += " AND sender_id=?"
                values.append(str(counterpart_id))
            row = conn.execute(
                f"""SELECT sender_id, COALESCE(NULLIF(MAX(sender_name),''),NULLIF(sender_id,''),'未知成员') AS name,
                           COUNT(*) AS n FROM messages m WHERE {scope}{time_sql}
                    AND COALESCE(is_self,0)=0 AND COALESCE(sender_id,'')!=''
                    GROUP BY sender_id ORDER BY n DESC,name COLLATE NOCASE,sender_id LIMIT 1""",
                values + time_values,
            ).fetchone()
            peer_id = str(counterpart_id) if counterpart_id is not None else (row["sender_id"] if row else None)
            response_times = []
            if peer_id is not None:
                response_scope = "platform=? AND account_id=? AND conversation_id=?"
                response_values = [str(platform), str(account_id), str(conversation_id)]
                response_bounds_parts, response_date_values = [], []
                if lower is not None:
                    response_bounds_parts.append("timestamp>=?")
                    response_date_values.append(lower)
                if upper is not None:
                    response_bounds_parts.append("timestamp<=?")
                    response_date_values.append(upper)
                response_bounds = " AND " + " AND ".join(response_bounds_parts) if response_bounds_parts else ""
                response_rows = conn.execute(
                    f"""WITH ordered AS (
                           SELECT timestamp,is_self,sender_id,
                                  LAG(timestamp) OVER (ORDER BY timestamp,rowid) AS previous_timestamp,
                                  LAG(is_self) OVER (ORDER BY timestamp,rowid) AS previous_is_self,
                                  LAG(sender_id) OVER (ORDER BY timestamp,rowid) AS previous_sender_id
                           FROM messages m WHERE {response_scope}{response_bounds}
                       )
                       SELECT timestamp-previous_timestamp AS seconds FROM ordered
                       WHERE COALESCE(is_self,0)=1 AND COALESCE(previous_is_self,0)=0
                         AND previous_sender_id=? AND previous_timestamp IS NOT NULL
                         AND timestamp-previous_timestamp>=0 AND timestamp-previous_timestamp<=3600
                         AND timestamp-previous_timestamp<86400
                       ORDER BY timestamp""",
                    response_values + response_date_values + [peer_id],
                ).fetchall()
                response_times = [int(item["seconds"]) for item in response_rows]
        report = self._period_report(year, platform, account_id, conversation_id, lower, upper)
        peer_name = row["name"] if row else (peer_id or "对方")
        peer_count = int(row["n"]) if row and (counterpart_id is None or row["sender_id"] == peer_id) else 0
        return PairReport(
            str(platform), str(account_id), str(conversation_id), conv["title"], year,
            report.total_messages, report.sent_messages, report.received_messages, report.sent_ratio, peer_id, peer_name,
            peer_count, len(response_times),
            int(round(sum(response_times) / len(response_times))) if response_times else None,
            min(response_times) if response_times else None,
            report.active_days, report.first_timestamp, report.last_timestamp,
            report.hourly, report.message_types,
        )
    def relationship_footprint(self, platform="all", account_id=None, start=None, end=None,
                               limit=500, offset=0):
        """Summarize interaction volume by conversation without exposing message content."""
        lower, upper = self._bounds(start, end)
        scope, params = self._scope(platform, account_id)
        bounds, date_params = self._date_predicate(lower, upper)
        limit = max(0, min(int(limit), 5000))
        offset = max(0, int(offset))
        with self.store._lock:
            conn = self.store._require_connection()
            rows = conn.execute(
                f"""SELECT m.platform, m.account_id, m.conversation_id,
                           COALESCE(c.title,m.conversation_id) AS title,
                           COALESCE(c.kind,'unknown') AS kind, COUNT(*) AS message_count,
                           SUM(CASE WHEN COALESCE(m.is_self,0)=1 THEN 1 ELSE 0 END) AS sent_count,
                           SUM(CASE WHEN COALESCE(m.is_self,0)=0 THEN 1 ELSE 0 END) AS received_count,
                           COUNT(DISTINCT date(m.timestamp,'unixepoch','+8 hours')) AS active_days,
                           MIN(m.timestamp) AS first_timestamp, MAX(m.timestamp) AS last_timestamp,
                           SUM(CASE WHEN {media_message_predicate('m')} THEN 1 ELSE 0 END) AS media_count
                    FROM messages m LEFT JOIN conversations c
                      ON c.platform=m.platform AND c.account_id=m.account_id AND c.id=m.conversation_id
                    WHERE {scope}{bounds}
                    GROUP BY m.platform,m.account_id,m.conversation_id
                    ORDER BY last_timestamp DESC,title COLLATE NOCASE,m.conversation_id
                    LIMIT ? OFFSET ?""", params + date_params + [limit, offset]
            ).fetchall()
        return [{
            "platform": row["platform"], "account_id": row["account_id"],
            "conversation_id": row["conversation_id"], "title": row["title"], "kind": row["kind"],
            "message_count": int(row["message_count"]), "sent_count": int(row["sent_count"] or 0),
            "received_count": int(row["received_count"] or 0), "active_days": int(row["active_days"] or 0),
            "first_timestamp": row["first_timestamp"], "last_timestamp": row["last_timestamp"],
            "media_count": int(row["media_count"] or 0),
        } for row in rows]

    def relationship_timeline(self, platform, account_id, conversation_id, start=None, end=None,
                              limit=366, offset=0):
        """Return per-day inbound/outbound counts in UTC+08, never message bodies."""
        lower, upper = self._bounds(start, end)
        bounds, date_params = self._date_predicate(lower, upper)
        limit = max(0, min(int(limit), 5000))
        offset = max(0, int(offset))
        with self.store._lock:
            conn = self.store._require_connection()
            rows = conn.execute(
                f"""SELECT date(m.timestamp,'unixepoch','+8 hours') AS local_date,
                           COUNT(*) AS message_count,
                           SUM(CASE WHEN COALESCE(m.is_self,0)=1 THEN 1 ELSE 0 END) AS sent_count,
                           SUM(CASE WHEN COALESCE(m.is_self,0)=0 THEN 1 ELSE 0 END) AS received_count,
                           SUM(CASE WHEN {media_message_predicate('m')} THEN 1 ELSE 0 END) AS media_count
                    FROM messages m
                    WHERE m.platform=? AND m.account_id=? AND m.conversation_id=?{bounds}
                    GROUP BY local_date ORDER BY local_date DESC LIMIT ? OFFSET ?""",
                [str(platform), str(account_id), str(conversation_id)] + date_params + [limit, offset]
            ).fetchall()
        return [{
            "date": row["local_date"], "message_count": int(row["message_count"]),
            "sent_count": int(row["sent_count"] or 0), "received_count": int(row["received_count"] or 0),
            "media_count": int(row["media_count"] or 0),
        } for row in rows]

    def relationship_achievements(self, platform, account_id, conversation_id):
        """Derive objective, content-free milestones from one local conversation."""
        platform, account_id, conversation_id = str(platform), str(account_id), str(conversation_id)
        with self.store._lock:
            conn = self.store._require_connection()
            conversation = conn.execute(
                "SELECT COALESCE(kind,'unknown') AS kind FROM conversations "
                "WHERE platform=? AND account_id=? AND id=?",
                (platform, account_id, conversation_id),
            ).fetchone()
            if not conversation:
                raise KeyError("conversation not found")
            if str(conversation["kind"]).lower() not in self.PRIVATE_CONVERSATION_KINDS:
                return {"available": False, "reason": "关系成就仅适用于私聊会话", "items": []}
            summary = conn.execute(
                """SELECT COUNT(*) AS total, MIN(timestamp) AS first_timestamp,
                          MAX(timestamp) AS last_timestamp,
                          CAST(julianday(date(MAX(timestamp),'unixepoch','+8 hours')) -
                               julianday(date(MIN(timestamp),'unixepoch','+8 hours')) AS INTEGER) AS span_days,
                          SUM(CASE WHEN (type='image' AND NOT (
                                   platform='wechat' AND COALESCE(raw_type,'') IN ('47','11000')
                               )) OR """ + _has_attachment_type('m', ('image', 'photo', 'picture', 'images', '图片')) + """ THEN 1 ELSE 0 END) AS images,
                          SUM(CASE WHEN type='voice' OR """ + _has_attachment_type('m', ('voice', 'audio', '语音')) + """ THEN 1 ELSE 0 END) AS voices,
                          SUM(CASE WHEN type='video' OR """ + _has_attachment_type('m', ('video', 'videos', '视频')) + """ THEN 1 ELSE 0 END) AS videos,
                          SUM(CASE WHEN type='file' OR """ + _has_attachment_type('m', ('file', 'files', '文件')) + """ THEN 1 ELSE 0 END) AS files,
                          SUM(CASE WHEN platform='wechat' AND COALESCE(raw_type,'')='50'
                                   THEN 1 ELSE 0 END) AS calls
                   FROM messages m WHERE platform=? AND account_id=? AND conversation_id=?""",
                (platform, account_id, conversation_id),
            ).fetchone()
            activity = conn.execute(
                """WITH active_dates AS (
                       SELECT date(timestamp,'unixepoch','+8 hours') AS day
                       FROM messages WHERE platform=? AND account_id=? AND conversation_id=?
                       GROUP BY day
                   ), islands AS (
                       SELECT day, CAST(julianday(day) AS INTEGER) -
                              ROW_NUMBER() OVER (ORDER BY day) AS island
                       FROM active_dates
                   ), streaks AS (
                       SELECT island, COUNT(*) AS days FROM islands GROUP BY island
                   )
                   SELECT (SELECT COUNT(*) FROM active_dates) AS active_days,
                          (SELECT COUNT(DISTINCT substr(day,1,4)) FROM active_dates) AS active_years,
                          COALESCE((SELECT MAX(days) FROM streaks),0) AS longest_streak""",
                (platform, account_id, conversation_id),
            ).fetchone()

        first_timestamp = summary["first_timestamp"]
        last_timestamp = summary["last_timestamp"]
        span_days = max(0, int(summary["span_days"] or 0))
        metrics = {
            "first_message": int(summary["total"] or 0) > 0,
            "messages": int(summary["total"] or 0),
            "active_days": int(activity["active_days"] or 0),
            "streak_days": int(activity["longest_streak"] or 0),
            "images": int(summary["images"] or 0),
            "voices": int(summary["voices"] or 0),
            "videos": int(summary["videos"] or 0),
            "files": int(summary["files"] or 0),
            "calls": int(summary["calls"] or 0),
            "span_days": span_days,
            "active_years": int(activity["active_years"] or 0),
        }
        definitions = [
            ("first_message", "第一条记录", "已发现第一条本地消息", 1, "first_message"),
            ("messages_100", "百条对话", "私聊消息达到 100 条", 100, "messages"),
            ("messages_1000", "千条对话", "私聊消息达到 1,000 条", 1000, "messages"),
            ("messages_10000", "万条对话", "私聊消息达到 10,000 条", 10000, "messages"),
            ("messages_50000", "长篇对话", "私聊消息达到 50,000 条", 50000, "messages"),
            ("active_days_7", "七日足迹", "在 7 个不同自然日有消息", 7, "active_days"),
            ("active_days_30", "三十日足迹", "在 30 个不同自然日有消息", 30, "active_days"),
            ("active_days_100", "百日足迹", "在 100 个不同自然日有消息", 100, "active_days"),
            ("active_days_365", "年度足迹", "在 365 个不同自然日有消息", 365, "active_days"),
            ("streak_3", "连续三日", "连续 3 个自然日有消息", 3, "streak_days"),
            ("streak_7", "连续一周", "连续 7 个自然日有消息", 7, "streak_days"),
            ("streak_30", "连续一月", "连续 30 个自然日有消息", 30, "streak_days"),
            ("images_100", "百张图片", "本地索引中有 100 条图片消息", 100, "images"),
            ("voices_50", "五十条语音", "本地索引中有 50 条语音消息", 50, "voices"),
            ("videos_20", "二十条视频", "本地索引中有 20 条视频消息", 20, "videos"),
            ("files_10", "十份文件", "本地索引中有 10 条文件消息", 10, "files"),
            ("calls_5", "五次通话", "本地记录中有 5 次语音或视频通话", 5, "calls"),
            ("span_365", "记录满一年", "首末本地记录相隔至少 365 日", 365, "span_days"),
            ("active_years_2", "跨越两年", "在 2 个不同自然年有消息", 2, "active_years"),
            ("active_years_3", "跨越三年", "在 3 个不同自然年有消息", 3, "active_years"),
            ("active_years_5", "跨越五年", "在 5 个不同自然年有消息", 5, "active_years"),
        ]
        items = [{
            "id": identifier, "title": title, "description": description,
            "current": int(metrics[key]) if key != "first_message" else int(bool(metrics[key])),
            "threshold": threshold,
            "unlocked": bool(metrics[key]) if key == "first_message" else int(metrics[key]) >= threshold,
        } for identifier, title, description, threshold, key in definitions]
        return {"available": True, "coverage": "仅基于当前本机已索引记录", "items": items}

    def _period_report(self, year, platform, account_id, conversation_id, lower, upper):
        scope, params = self._scope(platform, account_id, conversation_id)
        bounds, date_params = self._date_predicate(lower, upper)
        params += date_params
        with self.store._lock:
            conn = self.store._require_connection()
            summary = conn.execute(
                f"""SELECT COUNT(*) AS total,
                    SUM(CASE WHEN is_self=1 THEN 1 ELSE 0 END) AS sent,
                    SUM(CASE WHEN is_self=0 THEN 1 ELSE 0 END) AS received,
                    COUNT(DISTINCT date(timestamp,'unixepoch','+8 hours')) AS active_days,
                    MIN(timestamp) AS first_timestamp,MAX(timestamp) AS last_timestamp
                    FROM messages m WHERE {scope}{bounds}""", params
            ).fetchone()
            monthly = conn.execute(
                f"""SELECT CAST(strftime('%m',timestamp,'unixepoch','+8 hours') AS INTEGER) AS month,COUNT(*) AS count
                    FROM messages m WHERE {scope}{bounds} GROUP BY month ORDER BY month""", params
            ).fetchall()
            hourly = conn.execute(
                f"""SELECT CAST(strftime('%H',timestamp,'unixepoch','+8 hours') AS INTEGER) AS hour,COUNT(*) AS count
                    FROM messages m WHERE {scope}{bounds} GROUP BY hour ORDER BY hour""", params
            ).fetchall()
            types = conn.execute(
                f"""SELECT COALESCE(type,'unknown') AS type,COUNT(*) AS count
                    FROM messages m WHERE {scope}{bounds} GROUP BY type ORDER BY count DESC,type""", params
            ).fetchall()
            busiest_day = conn.execute(
                f"""SELECT date(timestamp,'unixepoch','+8 hours') AS date,COUNT(*) AS count
                    FROM messages m WHERE {scope}{bounds} GROUP BY date ORDER BY count DESC,date LIMIT 1""", params
            ).fetchone()
            busiest_hour = conn.execute(
                f"""SELECT CAST(strftime('%H',timestamp,'unixepoch','+8 hours') AS INTEGER) AS hour,COUNT(*) AS count
                    FROM messages m WHERE {scope}{bounds} GROUP BY hour ORDER BY count DESC,hour LIMIT 1""", params
            ).fetchone()
            heatmap_rows = conn.execute(
                f"""SELECT CAST(strftime('%w',timestamp,'unixepoch','+8 hours') AS INTEGER) AS weekday,
                           CAST(strftime('%H',timestamp,'unixepoch','+8 hours') AS INTEGER) AS hour,COUNT(*) AS count
                    FROM messages m WHERE {scope}{bounds} GROUP BY weekday,hour""", params
            ).fetchall()
            active_dates = conn.execute(
                f"""SELECT date(timestamp,'unixepoch','+8 hours') AS day,COUNT(*) AS count
                    FROM messages m WHERE {scope}{bounds} GROUP BY day ORDER BY day""", params
            ).fetchall()
            top_contacts = conn.execute(
                f"""SELECT m.platform, COALESCE(NULLIF(MAX(c.name),''), NULLIF(MAX(m.sender_name),''),
                           '未知联系人') AS name, COUNT(*) AS count
                    FROM messages m LEFT JOIN contacts c
                      ON c.platform=m.platform AND c.account_id=m.account_id AND c.id=m.sender_id
                    WHERE {scope}{bounds} AND COALESCE(m.is_self,0)=0
                    GROUP BY m.platform,m.account_id,m.sender_id
                    ORDER BY count DESC,name COLLATE NOCASE,m.platform LIMIT 10""", params
            ).fetchall()
        monthly_counts = {int(row["month"]): int(row["count"]) for row in monthly}
        heatmap = [[0] * 24 for _ in range(7)]
        for row in heatmap_rows:
            if row["weekday"] is not None and row["hour"] is not None:
                heatmap[int(row["weekday"])][int(row["hour"])] = int(row["count"])
        longest_streak = None
        streak_start = streak_end = previous_day = None
        streak_days = best_days = 0
        best_start = best_end = None
        for row in active_dates:
            if not row["day"]:
                continue
            current_day = date.fromisoformat(row["day"])
            if previous_day and current_day == previous_day + timedelta(days=1):
                streak_days += 1
                streak_end = current_day
            else:
                streak_start = streak_end = current_day
                streak_days = 1
            if streak_days > best_days:
                best_days = streak_days
                best_start, best_end = streak_start, streak_end
            previous_day = current_day
        if best_days:
            longest_streak = {
                "days": best_days,
                "start_date": best_start.isoformat(),
                "end_date": best_end.isoformat(),
            }
        return AnnualReport(
            str(platform), str(account_id) if account_id is not None else "all",
            int(year) if year is not None else None,
            str(conversation_id) if conversation_id is not None else None,
            int(summary["total"] or 0), int(summary["sent"] or 0), int(summary["received"] or 0),
            round((summary["sent"] or 0) / summary["total"], 6) if summary["total"] else 0.0,
            int(summary["active_days"] or 0), summary["first_timestamp"], summary["last_timestamp"],
            tuple({"month": month, "count": monthly_counts.get(month, 0)} for month in range(1, 13)),
            tuple({"hour": int(r["hour"]), "count": int(r["count"])} for r in hourly),
            tuple({"type": r["type"], "count": int(r["count"])} for r in types),
            {"date": busiest_day["date"], "count": int(busiest_day["count"])} if busiest_day else None,
            {"hour": int(busiest_hour["hour"]), "count": int(busiest_hour["count"])} if busiest_hour else None,
            tuple(tuple(hours) for hours in heatmap), longest_streak,
            tuple({"platform": row["platform"], "name": row["name"], "count": int(row["count"])}
                  for row in top_contacts),
        )
