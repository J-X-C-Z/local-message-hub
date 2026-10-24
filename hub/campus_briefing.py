"""Deterministic, read-only student briefing over the existing campus archive."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import re
import unicodedata
from urllib.parse import urlsplit


CHINA = timezone(timedelta(hours=8))
TOPICS = {
    'scholarships': ('奖学金与奖助', ('奖学金', '奖助', '助学金', '困难补助', '国家励志')),
    'competitions': ('竞赛与实践', ('竞赛', '大赛', '挑战杯', '创新创业', '比赛', '科创')),
    'courses': ('选课与教务', ('选课', '课程', '补退选', '教务', '考试', '学分', '教学')),
    'campus': ('校园生活', ('校区', '校园', '图书馆', '宿舍', '停水', '停电', '食堂')),
    'career': ('实习与发展', ('实习', '招聘', '就业', '交流项目')),
}
SECTIONS = {'selected', 'all', 'hot', 'daily', 'topics', 'bookmarks', 'sources', 'pipeline'}


def _normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', str(value)).casefold()
                   if c.isalnum())


def _published(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=CHINA) if parsed.tzinfo is None else parsed.astimezone(CHINA)
    except (ValueError, TypeError, AttributeError):
        return None


def _tier(source):
    if source.get('kind') == 'local_wechat' and source.get('account_id') and source.get('contact_id'):
        return 'verified_official_account', '本机已核验公众号'
    host = (urlsplit(source.get('url', '')).hostname or '').lower()
    if host == 'sysu.edu.cn' or host.endswith('.sysu.edu.cn'):
        return 'official_web', '学校官方站点'
    return 'unclassified', '未分级来源'


def _material(item):
    if item.get('content_status') == 'full_text' and item.get('content', '').strip():
        return 'full_text'
    if item.get('content_status') == 'fetch_error':
        return 'fetch_error'
    return 'summary_only' if (item.get('summary') or item.get('content') or '').strip() else 'title_only'


def _topics(item):
    text = ' '.join(str(item.get(k) or '') for k in ('title', 'summary', 'content'))
    return [key for key, (_, words) in TOPICS.items() if any(w in text for w in words)]


def _rank(item, source, profile, now):
    if not any(str(item.get(key) or '').strip() for key in ('summary', 'content')):
        return 0, ['材料不足：仅有标题，待补齐正文或摘要'], False
    text = ' '.join(str(item.get(k) or '') for k in ('title', 'summary', 'content'))
    title = item.get('title', '')
    reasons, score = [], 0
    for key in _topics(item):
        if key in ('scholarships', 'competitions', 'courses'):
            reasons.append('关注主题：' + TOPICS[key][0])
            score += 20
    for keyword in profile.get('keywords', []):
        if keyword and keyword in text:
            reasons.append('档案关键词：' + keyword)
            score += 5
    if '本科' in text:
        reasons.append('正文或标题明确提及本科')
        score += 10
    faculty = profile.get('faculty', '')
    if faculty and faculty in (text + ' ' + str(item.get('faculty', ''))):
        reasons.append('匹配学院：' + faculty)
        score += 10
    campuses = [c for c in (profile.get('campus'), profile.get('next_campus')) if c]
    scope = item.get('campus') or source.get('scope') or ''
    restricted_campus = scope not in ('', 'all', '全校', '全部')
    normalize_campus = lambda value: value.replace('校区', '校园')
    matched_campuses = [c for c in campuses if normalize_campus(c) in normalize_campus(scope if restricted_campus else text)]
    for campus in matched_campuses:
        reasons.append('匹配当前或下学期校区：' + campus)
        score += 5
    if _tier(source)[0] != 'unclassified':
        reasons.append('来源：' + _tier(source)[1])
        score += 10
    if _material(item) == 'full_text':
        reasons.append('已有真实正文')
        score += 5
    level = item.get('level') or source.get('level', '')
    # A broad news mention of postgraduate study is not an eligibility exclusion.
    postgraduate = level in ('研究生', '硕士', '博士') or (
        '本科' not in item.get('title', '') and ('研究生' in item.get('title', '') or re.search(r'(?:硕士|博士)(?:生|研究生)', item.get('title', ''))))
    eligible = not (profile.get('level') == '本科' and postgraduate)
    if not eligible:
        reasons.append('未入选：限定研究生培养层次')
    if campuses and restricted_campus and not matched_campuses:
        eligible = False
        reasons.append('未入选：校区范围与当前及下学期校区不符')
    teacher_result = ('教师' in title or '教学团队' in title) and any(w in title for w in ('竞赛', '教学比赛')) and any(w in title for w in ('一等奖', '获奖', '佳绩', '荣获', '斩获', '再创'))
    meeting_recap = any(w in title for w in ('召开', '举行', '举办')) and any(w in title for w in ('研讨会', '座谈会', '工作会', '会议')) and not any(w in title for w in ('报名', '招募', '邀请学生'))
    if profile.get('level') == '本科' and (teacher_result or meeting_recap):
        eligible = False
        reasons.append('未入选：教师竞赛成果或会议报道，非本科生办理事项')
    action_text = title + ' ' + (item.get('summary') or '')
    actions = [word for word in ('报名', '申请', '选课', '补退选', '评选', '公示', '通知', '安排', '招募', '申报', '征集') if word in action_text]
    if actions:
        reasons.append('学生办理线索：' + '、'.join(actions))
        score += 20
    published = _published(item.get('published_at'))
    age_days = (now - published).total_seconds() / 86400 if published else None
    if published and published.date() > now.date():
        eligible = False
        reasons.append('未入选：来源日期晚于今天，尚不能确认为已发布信息')
    if age_days is not None and 0 <= age_days <= 90:
        days, boost = (7, 12) if age_days <= 7 else (30, 8) if age_days <= 30 else (90, 4)
        reasons.append(f'发布日期在近 {days} 天内')
        score += boost
    material = _material(item)
    selected = eligible and material != 'title_only' and bool(set(_topics(item)) & {'scholarships', 'competitions', 'courses'})
    return min(score, 100), reasons, selected


def group_materials(items, now=None):
    """Shared untruncated groups for read views and atomic event-state updates."""
    now = (now or datetime.now(CHINA)).astimezone(CHINA)
    groups, by_signature = [], {}
    dated = {item['id']: _published(item.get('published_at')) for item in items}
    # Exact normalized title and extractive summary only: uncertain near matches stay separate.
    for item in sorted(items, key=lambda v: (v.get('published_at', ''), v['id'])):
        title = _normalized(item.get('title', ''))
        summary = _normalized(item.get('summary') or item.get('content') or '')
        signature = (title, summary[:240], tuple(re.findall(r'\d+', summary)),
                     *(item.get(key, '') for key in ('campus', 'faculty', 'level')),
                     bool(dated[item['id']] and dated[item['id']].date() > now.date()))
        group = None
        if len(title) >= 8 and summary and dated[item['id']]:
            group = next((g for g in by_signature.get(signature, [])
                          if dated[g[0]['id']] and abs((dated[item['id']] - dated[g[0]['id']]).total_seconds()) <= 48 * 3600), None)
        if group is None:
            group = []
            groups.append(group)
            by_signature.setdefault(signature, []).append(group)
        group.append(item)
    return groups


def build_briefing(items, sources, profile, updates, section='selected', q='', topic='', date='', limit=50, offset=0, *, now=None, scoring=None):
    if section not in SECTIONS or any(not isinstance(v, str) or len(v) > 500 for v in (q, topic, date)):
        raise ValueError('信息站筛选条件无效')
    if topic and topic not in TOPICS:
        raise ValueError('信息主题不存在')
    if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
        raise ValueError('分页参数无效')
    now = (now or datetime.now(CHINA)).astimezone(CHINA)
    if date:
        try:
            if datetime.strptime(date, '%Y-%m-%d').date().isoformat() != date:
                raise ValueError
        except ValueError:
            raise ValueError('日期须为 YYYY-MM-DD') from None
    elif section == 'daily':
        date = now.date().isoformat()
    source_map = {s['id']: s for s in sources}
    public_sources = [dict(s, tier=_tier(s)[0], tier_label=_tier(s)[1]) for s in sources]
    dated = {item['id']: _published(item.get('published_at')) for item in items}
    scoring = scoring or {'enabled': False}
    ranked = {}
    for item in items:
        rule_score, rule_reasons, rule_selected = _rank(item, source_map.get(item['source_id'], {}), profile, now)
        model = item.get('scoring', {'status': 'pending' if scoring['enabled'] else 'disabled'})
        model_enabled = scoring['enabled']
        completed = model_enabled and model.get('status') == 'completed'
        published = dated[item['id']]
        future = bool(published and published.date() > now.date())
        ranked[item['id']] = dict(
            score=model['score'] if completed else 0 if model_enabled else rule_score,
            selected=bool(model.get('selected') and not future) if completed else False if model_enabled else rule_selected,
            reasons=([f"双模型平均 {model['score']} 分"] if completed else [{'error': '西湖双模型评分失败，待重试', 'insufficient': '材料不足或来源不支持，暂不评分'}.get(model.get('status'), '西湖双模型待评分')] if model_enabled else rule_reasons),
            rule_score=rule_score, scoring=model, score_method='westlake_dual_models' if completed else 'pending' if model_enabled else 'local_rules')
    groups = group_materials(items, now)
    events = []
    for group in groups:
        scored = [(item, ranked[item['id']]['score'], ranked[item['id']]['reasons'], ranked[item['id']]['selected']) for item in group]
        scored.sort(key=lambda v: (v[3] if scoring['enabled'] else False, ranked[v[0]['id']]['score_method'] == 'westlake_dual_models', _tier(source_map.get(v[0]['source_id'], {}))[0] == 'official_web', _material(v[0]) == 'full_text', v[1], v[0].get('published_at', ''), v[0]['id']), reverse=True)
        item, score, reasons, _ = scored[0]
        reports, participants, provenance_urls = [], {}, {}
        for report in group:
            report_date = dated[report['id']]
            provenance = report.get('provenance') or [dict(source_id=report['source_id'], source_name=report.get('source_name', ''), url=report['url'])]
            for origin in provenance:
                sid = origin['source_id']
                provenance_urls.setdefault(sid, set()).add(origin.get('url') or report['url'])
                origin_date = _published(origin['published_at']) if 'published_at' in origin else report_date
                age = (now - origin_date).total_seconds() / 3600 if origin_date else None
                weight = 2 ** (-age / 24) if age is not None and 0 <= age <= 48 else 0
                participants[sid] = max(participants.get(sid, 0), weight)
            reports.append({key: report.get(key, '') for key in ('id', 'title', 'url', 'source_id', 'source_name', 'published_at', 'content_status')})
        pub = dated[item['id']]
        text = ' '.join((item.get('summary') or item.get('content') or '').split())
        events.append(dict(id=item['id'], representative_id=item['id'], title=item.get('title', ''),
                           summary=text[:240], url=item['url'], published_at=item.get('published_at', ''),
                           topics=sorted({t for report in group for t in _topics(report)}), score=score, reasons=reasons,
                           rule_score=ranked[item['id']]['rule_score'], scoring=ranked[item['id']]['scoring'], score_method=ranked[item['id']]['score_method'],
                           heat=round(sum(participants.values()), 3), source_count=len(participants), hot_source_count=sum(w > 0 for w in participants.values()), report_count=len(group),
                           material_status=_material(item), content_status=item.get('content_status', ''),
                           bookmarked=any(r.get('bookmarked') for r in group), read=all(r.get('read') for r in group),
                           reports=reports, provenance=[dict(source_id=sid, source_name=source_map.get(sid, {}).get('name', sid), urls=sorted(provenance_urls[sid])) for sid in sorted(participants)],
                           selected=any(v[3] for v in scored), date=pub.date().isoformat() if pub else '',
                           date_unknown=pub is None, future_date=bool(pub and pub.date() > now.date())))
    counts = dict(materials=len(items), events=len(events), selected=sum(e['selected'] for e in events),
                  unknown_date=sum(d is None for d in dated.values()), future_date=sum(bool(d and d.date() > now.date()) for d in dated.values()),
                  disabled_sources=sum(not s.get('enabled', True) for s in sources))
    counts.update(Counter(_material(item) for item in items))
    for key in ('full_text', 'summary_only', 'fetch_error', 'title_only'):
        counts.setdefault(key, 0)
    topic_counts = Counter(t for e in events for t in e['topics'])
    available_dates = sorted({d.date().isoformat() for d in dated.values() if d and d.date() <= now.date()}, reverse=True)
    selected = events
    if section == 'selected':
        selected = [e for e in selected if e['selected']]
    elif section == 'bookmarks':
        selected = [e for e in selected if e['bookmarked']]
    elif section == 'hot':
        selected = [e for e in selected if e['heat'] > 0]
    elif section in ('sources', 'pipeline'):
        selected = []
    if section == 'daily':
        # Calendar-day digest: include a group iff a report itself belongs to this day.
        selected = [dict(e, reports=[r for r in e['reports'] if dated[r['id']] and dated[r['id']].date() <= now.date() and dated[r['id']].date().isoformat() == date]) for e in selected]
        selected = [e for e in selected if e['reports']]
        for event in selected:
            representative = next(r for r in event['reports'] if r['id'] == event['representative_id']) if any(r['id'] == event['representative_id'] for r in event['reports']) else event['reports'][0]
            original = next(item for item in items if item['id'] == representative['id'])
            event.update(title=original['title'], url=original['url'], representative_id=original['id'], id=original['id'],
                         published_at=original.get('published_at', ''), summary=' '.join((original.get('summary') or original.get('content') or '').split())[:240], date=date)
            event.update(ranked[original['id']])
    if q:
        needle = q.casefold()
        selected = [e for e in selected if any(needle in (str(r.get('title', '')) + ' ' + str(r.get('source_name', ''))).casefold() for r in e['reports']) or needle in e['summary'].casefold()]
    if topic:
        selected = [e for e in selected if topic in e['topics']]
    if section == 'selected':
        selected.sort(key=lambda e: (e['published_at'], e['score'], e['id']), reverse=True)
    else:
        selected.sort(key=lambda e: ((e['heat'] if section == 'hot' else e['score'] if section == 'daily' else 0), e['published_at'], e['id']), reverse=True)
    page = []
    for event in selected[offset:offset + limit]:
        page.append(dict(event, reports=event['reports'][:50], reports_truncated=len(event['reports']) > 50,
                         provenance=[dict(p, urls=p['urls'][:50]) for p in event['provenance'][:50]],
                         provenance_truncated=len(event['provenance']) > 50 or any(len(p['urls']) > 50 for p in event['provenance'])))
    return dict(section=section, generated_at=now.isoformat(timespec='seconds'), date=date, available_dates=available_dates,
                items=page, total=len(selected), limit=limit, offset=offset, counts=counts,
                topics=[dict(id=key, name=value[0], count=topic_counts[key]) for key, value in TOPICS.items()],
                sources=public_sources, pipeline=dict(mode='westlake_dual_models' if scoring['enabled'] else 'local_rules',
                    description='西湖 DeepSeek 与 GLM 独立评分；双适用且均分达到 70 分入选' if scoring['enabled'] else '本地规则匹配、保守归组、原文摘录；未调用大模型', scoring=scoring,
                    stages=[dict(id='collected', name='已采集材料', count=len(items)), dict(id='full_text', name='真实正文', count=counts['full_text']),
                            dict(id='pending', name='待补齐正文', count=len(items) - counts['full_text']), dict(id='grouped', name='保守归组事件', count=len(events)),
                            dict(id='selected', name='符合关注主题', count=counts['selected'])], updates=updates))
