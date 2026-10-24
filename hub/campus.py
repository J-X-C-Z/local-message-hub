"""Local student information index with bounded incremental updates."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from urllib.parse import urlsplit, urlunsplit

from integrations.campus.catalog import SOURCES
from integrations.campus.adapter import fetch_source, is_official_url, is_wechat_article_url


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _canonical_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('无效的公开文章链接')
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or '/', parts.query, ''))


class CampusService:
    def __init__(self, storage: Path):
        storage = Path(storage)
        storage.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._work_lock = threading.RLock()
        self._closed = False
        self._thread = None
        self._update_thread = None
        self._score_thread = None
        self._update_stop = threading.Event()
        self._capability_reader = None
        self._status = dict(state='idle', started_at=None, finished_at=None,
                            processed=0, total=0, added=0, errors=[])
        self._db = sqlite3.connect(storage / 'campus.sqlite3', check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY, source_id TEXT NOT NULL, category TEXT NOT NULL,
                published_at TEXT NOT NULL, data TEXT NOT NULL,
                bookmarked INTEGER NOT NULL DEFAULT 0, read INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS campus_items_date ON items(published_at DESC,id);
            CREATE INDEX IF NOT EXISTS campus_items_source ON items(source_id);
            CREATE TABLE IF NOT EXISTS source_status (id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS campus_scores (id TEXT PRIMARY KEY,data TEXT NOT NULL);
        ''')

    def close(self):
        self.stop_updates()
        with self._lock:
            self._closed = True
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join()
        if self._score_thread and self._score_thread is not threading.current_thread():
            self._score_thread.join()
        with self._lock:
            self._db.close()

    def _setting(self, name, default):
        with self._lock:
            row = self._db.execute('SELECT data FROM settings WHERE id=?', (name,)).fetchone()
            return json.loads(row[0]) if row else default

    def _save_setting(self, name, value):
        with self._lock, self._db:
            self._db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (name, json.dumps(value, ensure_ascii=False)))

    def update_settings(self):
        values = dict(auto_updates_enabled=True, interval_minutes=15, fetch_full_text=True, batch_limit=20,
                      last_check=None, next_due=None, last_error='')
        values.update(self._setting('updates', {}))
        with self._lock:
            counts = dict(full_text=0, summary_only=0, error=0, pending=0)
            for row in self._db.execute('SELECT data FROM items'):
                item = json.loads(row[0])
                if is_wechat_article_url(item.get('url', '')):
                    status = item.get('content_status', 'summary_only')
                    counts['full_text' if status == 'full_text' else 'error' if status == 'fetch_error' else 'summary_only'] += 1
            counts['pending'] = counts['summary_only'] + counts['error']
            values.update(running=bool(self._update_thread and self._update_thread.is_alive()), content_counts=counts)
        return values

    def configure_updates(self, values):
        if not isinstance(values, dict) or set(values) - {'auto_updates_enabled', 'interval_minutes', 'fetch_full_text', 'batch_limit'}:
            raise ValueError('自动更新配置无效')
        for key, value in values.items():
            if key in ('auto_updates_enabled', 'fetch_full_text'):
                if type(value) is not bool:
                    raise ValueError('自动更新开关须为布尔值')
            elif type(value) is not int or not 1 <= value <= (1440 if key == 'interval_minutes' else 20):
                raise ValueError('自动更新间隔或批次无效')
        with self._lock:
            settings = self._setting('updates', {})
            settings.update(values)
            settings['next_due'] = _now()
            self._save_setting('updates', settings)
        return self.update_settings()

    def _scoring_cache(self):
        with self._lock:
            return {r['id']: json.loads(r['data']) for r in self._db.execute('SELECT * FROM campus_scores')}

    def _item_scoring(self, item, profile, cache, enabled=None):
        from hub.campus_scoring import fingerprint, scoreable
        if enabled is None:
            enabled = self._setting('scoring', {}).get('enabled', False)
        if not enabled:
            return {'status': 'disabled'}
        if not scoreable(item):
            return {'status': 'insufficient'}
        saved = cache.get(item['id'], {})
        return saved if saved.get('fingerprint') == fingerprint(item, profile) else {'status': 'pending'}

    def scoring_status(self):
        from hub.campus_scoring import MODELS
        with self._lock:
            settings = self._setting('scoring', {})
            profile, cache = self.profile(), self._scoring_cache()
            counts = dict(completed=0, pending=0, error=0, insufficient=0, disabled=0)
            for row in self._db.execute('SELECT id,data FROM items'):
                item = dict(json.loads(row['data']), id=row['id'])
                counts[self._item_scoring(item, profile, cache, settings.get('enabled', False))['status']] += 1
            return dict(enabled=settings.get('enabled', False), models=list(MODELS),
                        running=bool(self._score_thread and self._score_thread.is_alive()),
                        last_error=settings.get('last_error', ''), next_due=settings.get('next_due'), **counts)

    def configure_scoring(self, values):
        if not isinstance(values, dict) or set(values) != {'enabled'} or type(values['enabled']) is not bool:
            raise ValueError('请提供布尔类型的 enabled 评分开关')
        with self._lock:
            settings = self._setting('scoring', {})
            settings.update(enabled=values['enabled'], next_due=_now())
            self._save_setting('scoring', settings)
        if values['enabled']:
            self.start_scoring()
        return self.scoring_status()

    def start_scoring(self, *, automatic=False):
        with self._lock:
            if self._closed:
                raise ValueError('校园服务已关闭')
            if not self._setting('scoring', {}).get('enabled', False):
                raise ValueError('请先启用西湖模型评分')
            if not (self._score_thread and self._score_thread.is_alive()):
                self._score_thread = threading.Thread(target=self._score_pending, kwargs={'automatic': automatic},
                                                       daemon=True, name='campus-model-scoring')
                self._score_thread.start()
        return self.scoring_status()

    def _score_pending(self, *, automatic=False):
        from hub.campus_scoring import fingerprint, score_pair
        from hub.campus_briefing import _rank, CHINA
        with self._lock:
            profile, cache = self.profile(), self._scoring_cache()
            sources = {s['id']: s for s in self.sources()['items']}
            candidates = []
            for row in self._db.execute('SELECT id,data FROM items ORDER BY published_at DESC,id'):
                item = dict(json.loads(row['data']), id=row['id'])
                scoring = self._item_scoring(item, profile, cache, True)
                if scoring['status'] not in ('pending', 'error') or not sources.get(item['source_id'], {}).get('enabled', True):
                    continue
                if automatic and scoring.get('retry_after', '') > _now():
                    continue
                candidates.append(item)
            now = datetime.now(CHINA)
            candidates.sort(key=lambda i: (_rank(i, sources.get(i['source_id'], {}), profile, now)[2], i.get('published_at', '')), reverse=True)
        failed = False
        for item in candidates[:5]:
            if self._update_stop.is_set() or not self._setting('scoring', {}).get('enabled', False):
                break
            if not next((s.get('enabled', True) for s in self.sources()['items'] if s['id'] == item['source_id']), True):
                continue
            try:
                result = score_pair(item, profile)
            except Exception:
                failed = True
                result = dict(status='error', fingerprint=fingerprint(item, profile),
                              error='西湖评分未完成，请稍后重试',
                              retry_after=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(timespec='seconds'))
            with self._lock:
                row = self._db.execute('SELECT data FROM items WHERE id=?', (item['id'],)).fetchone()
                # Content/profile may change while the two model requests are in flight.
                if row and fingerprint(json.loads(row[0]), self.profile()) == result['fingerprint']:
                    with self._db:
                        self._db.execute('INSERT OR REPLACE INTO campus_scores VALUES (?,?)', (item['id'], json.dumps(result, ensure_ascii=False)))
        with self._lock:
            settings = self._setting('scoring', {})
            settings.update(last_error='部分西湖评分未完成，可稍后重试' if failed else '',
                            next_due=(datetime.now(timezone.utc) + timedelta(minutes=15 if failed or len(candidates) <= 5 else 1)).isoformat(timespec='seconds'))
            self._save_setting('scoring', settings)

    def start_updates(self, capability_reader=None):
        with self._lock:
            if self._closed:
                raise ValueError('校园服务已关闭')
            if self._update_thread and self._update_thread.is_alive():
                return
            self._capability_reader = capability_reader
            self._update_stop.clear()
            self._update_thread = threading.Thread(target=self._updates_loop, daemon=True, name='campus-updates')
            self._update_thread.start()

    def stop_updates(self):
        self._update_stop.set()
        thread = self._update_thread
        if thread and thread is not threading.current_thread():
            thread.join()

    def _updates_due(self):
        settings = self._setting('updates', {})
        if not settings.get('auto_updates_enabled', True):
            return False
        try:
            return not settings.get('next_due') or datetime.fromisoformat(settings['next_due']) <= datetime.now(timezone.utc)
        except (TypeError, ValueError):
            return True

    def _updates_loop(self):
        while not self._update_stop.is_set():
            try:
                enabled = self._capability_reader is None or bool(self._capability_reader())
            except Exception:
                enabled = False
            if enabled and self._updates_due():
                self.refresh()
            scoring = self._setting('scoring', {})
            if enabled and scoring.get('enabled') and (scoring.get('next_due') or '') <= _now():
                self.start_scoring(automatic=True)
            self._update_stop.wait(1)

    def sources(self):
        with self._lock:
            statuses = {r['id']: json.loads(r['data']) for r in self._db.execute('SELECT * FROM source_status')}
            counts = dict(self._db.execute('SELECT source_id, COUNT(*) FROM items GROUP BY source_id'))
            row = self._db.execute("SELECT data FROM settings WHERE id='wechat_sources'").fetchone()
            local_sources = json.loads(row[0]) if row else []
        overrides = self._setting('source_overrides', {})
        return {'items': [dict(s, **statuses.get(s['id'], dict(last_check=None, status='not_checked', error='')), item_count=counts.get(s['id'], 0), enabled=overrides.get(s['id'], s.get('enabled', True))) for s in [*SOURCES, *local_sources]]}

    def configure_source(self, source_id, values):
        if not isinstance(values, dict) or set(values) != {'enabled'} or type(values['enabled']) is not bool:
            raise ValueError('仅支持布尔类型的 enabled 开关')
        if not isinstance(source_id, str) or not any(s['id'] == source_id for s in self.sources()['items']):
            raise ValueError('信息来源不存在')
        with self._lock:
            overrides = self._setting('source_overrides', {})
            overrides[source_id] = values['enabled']
            self._save_setting('source_overrides', overrides)
        return next(s for s in self.sources()['items'] if s['id'] == source_id)

    def briefing(self, section='selected', q='', topic='', date='', limit=50, offset=0):
        from hub.campus_briefing import build_briefing
        with self._lock:
            profile = self.profile()
            cache = self._scoring_cache()
            enabled = self._setting('scoring', {}).get('enabled', False)
            items = [self._item(row, profile, cache, enabled) for row in self._db.execute('SELECT * FROM items')]
            sources = self.sources()['items']
            updates = self.update_settings()
            scoring = self.scoring_status()
        return build_briefing(items, sources, profile, updates, section, q, topic, date, limit, offset, scoring=scoring)

    def import_local_wechat(self, contact, report):
        """Store only an explicitly selected, locally verified official account."""
        source_id = 'wechat-local-' + hashlib.sha256(
            (contact['account_id'] + '\0' + contact['id']).encode()).hexdigest()[:24]
        source = dict(id=source_id, name=contact['name'], category='wechat',
                      category_name='微信公众号文章', kind='local_wechat', scope='all',
                      enabled=True, url='', account_id=contact['account_id'],
                      contact_id=contact['id'], alias=(contact.get('extra') or {}).get('alias', ''))
        with self._lock, self._db:
            row = self._db.execute("SELECT data FROM settings WHERE id='wechat_sources'").fetchone()
            sources = json.loads(row[0]) if row else []
            sources = [s for s in sources if s['id'] != source_id] + [source]
            self._db.execute("INSERT OR REPLACE INTO settings VALUES ('wechat_sources',?)",
                             (json.dumps(sources, ensure_ascii=False),))
        before = self.search(source_id=source_id)['total']
        self._store_result(source, {'status': 'ok' if report['items'] else 'empty', 'items': report['items']})
        total = self.search(source_id=source_id)['total']
        return dict(source_id=source_id, name=contact['name'], total=total,
                    added=max(0, total-before), invalid=report.get('invalid', 0))

    def profile(self):
        with self._lock:
            row = self._db.execute("SELECT data FROM settings WHERE id='profile'").fetchone()
        if row:
            profile = json.loads(row[0])
            profile.setdefault('next_campus', '')
            return profile
        return dict(campus='南校园', next_campus='东校园', faculty='电子与信息学院', level='本科', year='', keywords=['奖学金', '奖助', '竞赛', '选课'])

    def configure_profile(self, values):
        allowed = {'campus', 'next_campus', 'faculty', 'level', 'year', 'keywords'}
        if not isinstance(values, dict) or set(values) - allowed:
            raise ValueError('学生资料字段无效')
        for key, value in values.items():
            if key == 'keywords':
                if not isinstance(value, list) or len(value) > 30 or any(not isinstance(v, str) or not v.strip() or len(v) > 80 for v in value):
                    raise ValueError('关注词须为最多 30 个非空短文本')
            elif not isinstance(value, str) or len(value) > 100:
                raise ValueError('学生资料须为短文本')
        with self._lock:
            profile = self.profile()
            profile.update({k: list(dict.fromkeys(v.strip() for v in value)) if k == 'keywords' else value.strip() for k, value in values.items()})
            with self._db:
                self._db.execute("INSERT OR REPLACE INTO settings VALUES ('profile',?)", (json.dumps(profile, ensure_ascii=False),))
        return profile

    @staticmethod
    def _relevance(item, profile):
        reasons = []
        item_level = item.get('level', '')
        if not item_level:
            title = item.get('title', '')
            item_level = '本科' if '本科' in title else ('研究生' if '研究生' in title else '')
        profile_level = profile.get('level', '')
        if profile_level in ('硕士', '博士'):
            profile_level = '研究生'
        if item_level in ('硕士', '博士'):
            item_level = '研究生'
        if profile_level and item_level and item_level not in ('all', '全校', '全部') and profile_level != item_level:
            return []
        text = ' '.join(str(item.get(k, '')) for k in ('title', 'summary', 'content', 'tags')).casefold()
        scoped = False
        for key in ('campus', 'faculty', 'level'):
            scope = item_level if key == 'level' else (item.get(key) or '')
            if scope and scope not in ('all', '全校', '全部'):
                scoped = True
                matches = [profile_level if key == 'level' else profile[key]] + ([profile.get('next_campus', '')] if key == 'campus' else [])
                for value in matches:
                    if value and value.casefold() in str(scope).casefold():
                        label = {'campus': '校区', 'faculty': '学院', 'level': '培养层次'}[key]
                        reasons.append(f'{label}: {value}')
        if profile['faculty'] and profile['faculty'].casefold() in text:
            reasons.append(f"学院: {profile['faculty']}")
        reasons.extend(f'关注词: {word}' for word in profile['keywords'] if word.casefold() in text)
        if not scoped:
            reasons.insert(0, '全校公开信息')
        return reasons

    def _item(self, row, profile, scoring_cache=None, scoring_enabled=None):
        item = json.loads(row['data'])
        item.update(id=row['id'], bookmarked=bool(row['bookmarked']), read=bool(row['read']))
        item['relevance_reasons'] = self._relevance(item, profile)
        item['relevant'] = bool(item['relevance_reasons'])
        cache = scoring_cache if scoring_cache is not None else self._scoring_cache()
        item['scoring'] = self._item_scoring(item, profile, cache, scoring_enabled)
        return item

    def search(self, q='', category='', source_id='', campus='', relevant=False,
               bookmarked=False, limit=50, offset=0):
        if any(not isinstance(v, str) or len(v) > 500 for v in (q, category, source_id, campus)):
            raise ValueError('筛选条件无效')
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError('分页参数无效')
        if type(relevant) is not bool or type(bookmarked) is not bool:
            raise ValueError('筛选开关须为布尔值')
        clauses, params = [], []
        for key, value in (('category', category), ('source_id', source_id)):
            if value:
                clauses.append(f'{key}=?')
                params.append(value)
        if bookmarked:
            clauses.append('bookmarked=1')
        query = 'SELECT * FROM items' + (' WHERE ' + ' AND '.join(clauses) if clauses else '') + ' ORDER BY published_at DESC,id'
        with self._lock:
            profile = self.profile()
            rows = self._db.execute(query, params).fetchall()
            scoring_cache = self._scoring_cache()
            scoring_enabled = self._setting('scoring', {}).get('enabled', False)
        items = []
        for row in rows:
            item = self._item(row, profile, scoring_cache, scoring_enabled)
            if q and q.casefold() not in ' '.join(str(item.get(k, '')) for k in ('title', 'summary', 'content', 'source_name')).casefold():
                continue
            if campus and item.get('campus') not in ('', 'all', '全部', '全校', campus):
                continue
            if relevant and not item['relevant']:
                continue
            items.append(item)
        if relevant:
            # Python's stable sort retains publication order for equal relevance.
            items.sort(key=lambda item: sum(reason != '全校公开信息' for reason in item['relevance_reasons']), reverse=True)
        return dict(items=items[offset:offset + limit], total=len(items), profile=profile)

    def get_item(self, item_id):
        with self._lock:
            row = self._db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
            if row is None:
                raise ValueError('信息不存在')
            return self._item(row, self.profile())

    def set_item_state(self, item_id, values):
        if not isinstance(values, dict) or not values or set(values) - {'bookmarked', 'read'} or any(type(v) is not bool for v in values.values()):
            raise ValueError('仅支持布尔类型的 bookmarked/read 状态')
        with self._lock, self._db:
            self.get_item(item_id)
            self._db.execute('UPDATE items SET ' + ','.join(f'{key}=?' for key in values) + ' WHERE id=?', (*values.values(), item_id))
            return self.get_item(item_id)

    def set_event_state(self, item_id, values):
        from hub.campus_briefing import group_materials
        if not isinstance(item_id, str) or len(item_id) != 64 or any(c not in '0123456789abcdef' for c in item_id):
            raise ValueError('信息标识无效')
        if not isinstance(values, dict) or not values or set(values) - {'bookmarked', 'read'} or any(type(v) is not bool for v in values.values()):
            raise ValueError('仅支持布尔类型的 bookmarked/read 状态')
        with self._lock, self._db:
            self.get_item(item_id)
            profile = self.profile()
            items = [self._item(row, profile) for row in self._db.execute('SELECT * FROM items')]
            group = next(group for group in group_materials(items) if any(item['id'] == item_id for item in group))
            self._db.executemany('UPDATE items SET ' + ','.join(f'{key}=?' for key in values) + ' WHERE id=?',
                                 [(*values.values(), item['id']) for item in group])
            return dict(self.get_item(item_id), affected_count=len(group))

    def refresh_status(self):
        with self._lock:
            return dict(self._status, errors=list(self._status['errors']), updates=self.update_settings())

    def _begin_refresh(self):
        if self._closed:
            raise ValueError('校园服务已关闭')
        if self._status['state'] == 'running':
            return False
        self._status = dict(state='running', started_at=_now(), finished_at=None,
                            processed=0, total=0, added=0, errors=[], body_success=0, body_failed=0, backlog=0)
        return True

    def start_refresh(self):
        with self._lock:
            if self._begin_refresh():
                self._thread = threading.Thread(target=self._refresh, daemon=True, name='campus-refresh')
                self._thread.start()
            return self.refresh_status()

    def refresh(self):
        with self._lock:
            if not self._begin_refresh():
                return self.refresh_status()
        self._refresh()
        return self.refresh_status()

    def _store_result(self, source, result, count_status=True):
        status = result.get('status', 'ok')
        failed = status not in ('ok', 'success', 'empty')
        # Collector errors may contain remote response text; persist only a fixed message.
        error = '来源暂时不可用，请查看原站或稍后重试' if failed else ''
        added = 0
        with self._lock, self._db:
            if not failed:
                for raw in result.get('items', []):
                    try:
                        url = _canonical_url(raw.get('url', ''))
                    except (ValueError, TypeError):
                        continue
                    allowed = is_official_url(url) or is_wechat_article_url(url)
                    if not allowed:
                        continue
                    if not raw.get('title'):
                        continue
                    item_id = hashlib.sha256(url.encode()).hexdigest()
                    old_row = self._db.execute('SELECT data FROM items WHERE id=?', (item_id,)).fetchone()
                    old = json.loads(old_row[0]) if old_row else {}
                    item = dict(raw, url=url, source_id=source['id'], source_name=source['name'], category=source['category'])
                    provenance = {p['source_id']: p for p in old.get('provenance', []) if isinstance(p, dict) and p.get('source_id')}
                    if old.get('source_id'):
                        provenance.setdefault(old['source_id'], dict(source_id=old['source_id'], source_name=old.get('source_name', ''), url=old.get('url', url), published_at=old.get('published_at', '')))
                    provenance[source['id']] = dict(source_id=source['id'], source_name=source['name'], url=url,
                                                    published_at=raw.get('published_at') or old.get('published_at', ''))
                    item['provenance'] = list(provenance.values())
                    if old:
                        # Refreshing cards must retain the original identity and fetched body.
                        for field in ('source_id', 'source_name', 'category', 'content_attempted_at', 'content_error'):
                            if field in old:
                                item[field] = old[field]
                        if old.get('content_status') == 'full_text' or 'content_status' not in raw:
                            for field in ('content', 'content_status', 'content_source'):
                                if field in old:
                                    item[field] = old[field]
                        elif old.get('content_status') == 'fetch_error' and raw.get('content_status') == 'summary_only':
                            item['content_status'] = 'fetch_error'
                    for field in ('campus', 'faculty', 'level'):
                        item.setdefault(field, source.get(field, ''))
                    if not item['campus'] and source.get('scope', 'all') != 'all':
                        item['campus'] = source['scope']
                    published = raw.get('published_at') or old.get('published_at', '')
                    try:
                        if published:
                            datetime.fromisoformat(published)
                    except (ValueError, TypeError):
                        published = old.get('published_at', '')
                    item['published_at'] = published
                    added += self._db.execute('INSERT OR IGNORE INTO items (id,source_id,category,published_at,data) VALUES (?,?,?,?,?)', (item_id, source['id'], source['category'], published, json.dumps(item, ensure_ascii=False))).rowcount
                    self._db.execute('UPDATE items SET source_id=?,category=?,published_at=?,data=? WHERE id=?', (item['source_id'], item['category'], published, json.dumps(item, ensure_ascii=False), item_id))
            source_status = dict(last_check=_now(), status='error' if failed else status, error=error)
            self._db.execute('INSERT OR REPLACE INTO source_status VALUES (?,?)', (source['id'], json.dumps(source_status)))
            if self._status['state'] == 'running' and source.get('kind', 'feed') in ('feed', 'local_wechat'):
                self._status['processed'] += int(count_status)
                self._status['added'] += added
                if failed:
                    self._status['errors'].append(dict(source_id=source['id'], error=error))

    def ensure_full_text(self, item_id, force=True):
        from integrations.campus.adapter import fetch_wechat_article, read_cached_wechat_article
        with self._work_lock:
            item = self.get_item(item_id)
            if not is_wechat_article_url(item.get('url', '')):
                raise ValueError('仅公众号文章支持获取全文')
            if item.get('content_status') == 'full_text':
                return item
            body = None
            try:
                result = read_cached_wechat_article(item['url'])
                body = next((v for v in result.get('items', []) if v.get('content_status') == 'full_text' and v.get('content')), None)
            except Exception:
                pass
            if not body:
                try:
                    attempted = datetime.fromisoformat(item['content_attempted_at'])
                    if not force and datetime.now(timezone.utc) - attempted < timedelta(hours=6):
                        return item
                except (KeyError, ValueError, TypeError):
                    pass
                try:
                    result = fetch_wechat_article(item['url'])
                    body = next((v for v in result.get('items', []) if v.get('content_status') == 'full_text' and v.get('content')), None)
                except Exception:
                    pass
            with self._lock, self._db:
                row = self._db.execute('SELECT data FROM items WHERE id=?', (item_id,)).fetchone()
                saved = json.loads(row[0])
                saved['content_attempted_at'] = _now()
                if body:
                    saved.update(content=body['content'], content_status='full_text', content_source=body.get('content_source', 'public_article'), content_error='')
                    if not saved.get('published_at') and body.get('published_at'):
                        try:
                            datetime.fromisoformat(body['published_at'])
                            saved['published_at'] = body['published_at']
                        except (ValueError, TypeError):
                            pass
                else:
                    saved.update(content_status='fetch_error', content_error='正文暂时无法获取，请打开原文或稍后重试')
                self._db.execute('UPDATE items SET published_at=?,data=? WHERE id=?', (saved.get('published_at', ''), json.dumps(saved, ensure_ascii=False), item_id))
            return self.get_item(item_id)

    def _refresh_local(self, source):
        from integrations.wechat.official_accounts import get_cached_official_articles
        cursor_key = 'cursor:' + source['id']
        cursor = self._setting(cursor_key, {})
        while not self._update_stop.is_set():
            report = get_cached_official_articles(source['account_id'], source['contact_id'], cursor=cursor)
            if report.get('warning'):
                self._store_result(source, {'status': 'error'})
                return
            self._store_result(source, dict(status='ok' if report['items'] else 'empty', items=report['items']), count_status=False)
            next_cursor = report.get('next_cursor', cursor)
            # Persist only after the entire page has been committed; retries deduplicate by URL.
            self._save_setting(cursor_key, next_cursor)
            if not report.get('has_more') or next_cursor == cursor:
                with self._lock:
                    self._status['processed'] += 1
                return
            cursor = next_cursor

    def _enrich_pending(self):
        settings = self.update_settings()
        if not settings['fetch_full_text']:
            return
        disabled = {s['id'] for s in self.sources()['items'] if not s.get('enabled', True)}
        with self._lock:
            rows = self._db.execute('SELECT id,data FROM items ORDER BY published_at DESC,id').fetchall()
        candidates = []
        for row in rows:
            item = json.loads(row['data'])
            if item.get('source_id') in disabled or not is_wechat_article_url(item.get('url', '')) or item.get('content_status') == 'full_text':
                continue
            try:
                attempted = datetime.fromisoformat(item['content_attempted_at'])
                if datetime.now(timezone.utc) - attempted < timedelta(hours=6):
                    continue
            except (KeyError, ValueError, TypeError):
                pass
            candidates.append(row['id'])
        for item_id in candidates[:settings['batch_limit']]:
            if self._update_stop.is_set():
                break
            item = self.ensure_full_text(item_id, force=False)
            with self._lock:
                self._status['body_success' if item['content_status'] == 'full_text' else 'body_failed'] += 1
        with self._lock:
            self._status['backlog'] = self.update_settings()['content_counts']['pending']

    def import_wechat(self, url):
        from integrations.campus.adapter import fetch_wechat_article
        result = fetch_wechat_article(url)
        if not result.get('items') or result.get('status') not in ('ok', 'success'):
            raise ValueError('无法获取该公众号文章，请确认公开文章链接后重试')
        source = next((s for s in SOURCES if s['id'] == 'wechat-manual'),
                      dict(id='wechat-manual', name='微信公众号', category='wechat'))
        self._store_result(source, result)
        return self.get_item(hashlib.sha256(_canonical_url(result['items'][0]['url']).encode()).hexdigest())

    def _refresh(self):
        with self._work_lock:
            self._refresh_work()

    def _refresh_work(self):
        try:
            configured = self.sources()['items']
            sources = [s for s in configured if s.get('enabled', True) and s.get('kind', 'feed') == 'feed']
            with self._lock:
                local_sources = [s for s in configured if s.get('enabled', True) and s.get('kind') == 'local_wechat']
                self._status['total'] = len(sources) + len(local_sources)
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix='campus-source') as pool:
                jobs = {pool.submit(fetch_source, source): source for source in sources}
                for job in as_completed(jobs):
                    try:
                        result = job.result()
                    except Exception:
                        result = {'status': 'error', 'items': []}
                    try:
                        self._store_result(jobs[job], result)
                    except (ValueError, TypeError, KeyError):
                        self._store_result(jobs[job], {'status': 'error'})
            for source in local_sources:
                if self._update_stop.is_set():
                    break
                try:
                    self._refresh_local(source)
                except Exception:
                    self._store_result(source, {'status': 'error'})
            self._enrich_pending()
        except Exception:
            with self._lock:
                self._status['errors'].append(dict(source_id='', error='刷新失败，请稍后重试'))
        finally:
            with self._lock:
                self._status.update(state='completed' if not self._status['errors'] else 'completed_with_errors', finished_at=_now())
                settings = self._setting('updates', {})
                settings.update(last_check=_now(), next_due=(datetime.now(timezone.utc) + timedelta(minutes=self.update_settings()['interval_minutes'])).isoformat(timespec='seconds'),
                                last_error='部分来源更新失败，请稍后重试' if self._status['errors'] else '')
                self._save_setting('updates', settings)
