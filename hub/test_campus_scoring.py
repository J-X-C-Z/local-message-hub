"""Bounded public inputs and strict independent scoring; no real model calls."""
import json
import threading
import unittest
from unittest.mock import patch

from hub import campus_scoring as scoring


ITEM = dict(title='本科奖学金申请通知', summary='本科生可申请，10月30日截止。',
            url='https://jwb.sysu.edu.cn/info/1001/123.htm', content_status='summary_only')
PROFILE = dict(campus='南校园', next_campus='东校园', faculty='电子与信息学院',
               level='本科', keywords=['奖学金', '竞赛', '选课'])


def response(route, score=85, eligible=True, **changes):
    data = dict(model=route.rsplit('/', 1)[-1], choices=[dict(finish_reason='stop', message=dict(
        content=json.dumps(dict(score=score, eligible=eligible, reasons=['摘要说明本科生可申请']), ensure_ascii=False)))])
    data.update(changes)
    return data


class FakeResponse:
    def __init__(self, data):
        self.data = json.dumps(data, ensure_ascii=False).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size):
        return self.data[:size]


class CampusScoringTest(unittest.TestCase):
    def test_independent_parallel_requests_public_payload_and_mean(self):
        requests, barrier = [], threading.Barrier(2)

        def remote(request, timeout):
            payload = json.loads(request.data)
            requests.append(payload)
            self.assertEqual(request.full_url, scoring.BASE_URL + '/chat/completions')
            self.assertEqual(timeout, 90 if payload['model'].endswith('/glm-flash') else 45)
            self.assertEqual(payload['max_tokens'], 8192 if payload['model'].endswith('/glm-flash') else 2000)
            barrier.wait(timeout=3)
            return FakeResponse(response(payload['model'], 90 if payload['model'] == scoring.MODELS[0] else 80))

        item = dict(ITEM, content='正文' * 10000, content_status='full_text', contact_id='private-contact')
        profile = dict(PROFILE, name='private-name', email='private-email', token='private-token')
        with patch.object(scoring, 'urlopen', side_effect=remote):
            result = scoring.score_pair(item, profile)
        self.assertEqual(result['score'], 85)
        self.assertTrue(result['selected'])
        self.assertEqual(result['material_basis'], 'full_text')
        self.assertEqual([model['model'] for model in result['models']], list(scoring.MODELS))
        self.assertEqual(result['fingerprint'], scoring.fingerprint(item, profile))
        self.assertEqual(requests[0]['messages'], requests[1]['messages'])
        public = json.loads(requests[0]['messages'][1]['content'])
        self.assertEqual(len(public['article']['content']), 12000)
        self.assertTrue(public['article']['content_truncated'])
        self.assertNotIn('private-', json.dumps(requests))

    def test_one_ineligible_score_cannot_select(self):
        def remote(request, timeout):
            model = json.loads(request.data)['model']
            return FakeResponse(response(model, 95, model != scoring.MODELS[1]))
        with patch.object(scoring, 'urlopen', side_effect=remote):
            self.assertFalse(scoring.score_pair(ITEM, PROFILE)['selected'])

    def test_midnight_cannot_assign_next_day_key_to_previous_day_prompt(self):
        with patch.object(scoring, '_today', return_value='2026-10-23'):
            expected = scoring.fingerprint(ITEM, PROFILE)
        def remote(request, timeout):
            payload = json.loads(request.data)
            public = json.loads(payload['messages'][1]['content'])
            self.assertEqual(public['today_beijing'], '2026-10-23')
            return FakeResponse(response(payload['model']))
        with patch.object(scoring, '_today', side_effect=['2026-10-23', '2026-10-24']), patch.object(scoring, 'urlopen', side_effect=remote):
            self.assertEqual(scoring.score_pair(ITEM, PROFILE)['fingerprint'], expected)

    def test_public_boundary_and_title_only_rejected_without_network(self):
        bad = [dict(ITEM, summary='', content=' '), dict(ITEM, summary=ITEM['title'] + '。'),
               dict(ITEM, summary=ITEM['title'], content=ITEM['title']), dict(ITEM, url='https://evil.example/article'),
               dict(ITEM, url='http://sysu.edu.cn/a'), dict(ITEM, url='https://user:password@sysu.edu.cn/a'),
               dict(ITEM, url='https://sysu.edu.cn.evil.example/a'), dict(ITEM, url='https://mp.weixin.qq.com/not-article')]
        with patch.object(scoring, 'urlopen') as remote:
            for item in bad:
                self.assertFalse(scoring.scoreable(item))
                with self.assertRaises(ValueError):
                    scoring.score_pair(item, PROFILE)
            remote.assert_not_called()

    def test_prompt_urls_strip_credentials_and_profile_allows_only_public_fields(self):
        item = dict(ITEM, url=ITEM['url'] + '?access_token=secret#private')
        self.assertEqual(scoring._input(item, PROFILE)['article']['url'], ITEM['url'])
        article = dict(ITEM, url='https://mp.weixin.qq.com/s/abc?pass_ticket=secret&token=secret')
        self.assertTrue(scoring.scoreable(article))
        self.assertEqual(scoring._input(article, PROFILE)['article']['url'], 'https://mp.weixin.qq.com/s/abc')
        self.assertFalse(scoring._input(ITEM, PROFILE)['article']['content_truncated'])

    def test_fingerprint_changes_only_when_sent_public_material_profile_or_day_changes(self):
        with patch.object(scoring, '_today', return_value='2026-10-23'):
            original = scoring.fingerprint(ITEM, PROFILE)
            self.assertEqual(original, scoring.fingerprint(dict(ITEM, bookmarked=True, read=True, id='other'), dict(PROFILE, email='private')))
            self.assertNotEqual(original, scoring.fingerprint(dict(ITEM, summary='更新的申请截止日期'), PROFILE))
            self.assertNotEqual(original, scoring.fingerprint(ITEM, dict(PROFILE, campus='东校园')))
        with patch.object(scoring, '_today', return_value='2026-10-24'):
            self.assertNotEqual(original, scoring.fingerprint(ITEM, PROFILE))

    def test_invalid_or_missing_model_output_fails_pair_with_safe_error(self):
        invalid = [dict(model='other-model'), dict(choices=[]), dict(choices=[dict(finish_reason='length', message=dict(content='{}'))]),
                   dict(choices=[dict(finish_reason='stop', message=dict(content=None))]),
                   dict(choices=[dict(finish_reason='stop', message=dict(content='not JSON secret-token'))])]
        for override in invalid:
            def remote(request, timeout):
                model = json.loads(request.data)['model']
                return FakeResponse(response(model, **override))
            with self.subTest(override=override), patch.object(scoring, 'urlopen', side_effect=remote):
                with self.assertRaisesRegex(ValueError, '^西湖双模型评分失败，请稍后重试$'):
                    scoring.score_pair(ITEM, PROFILE)

    def test_strict_json_score_eligibility_and_reasons(self):
        invalid = [dict(score=True), dict(score=101), dict(score=1.5), dict(eligible='true'),
                   dict(reasons=[]), dict(reasons=['English only']), dict(reasons=['中文' * 100]),
                   dict(extra='secret')]
        for changes in invalid:
            def remote(request, timeout):
                model = json.loads(request.data)['model']
                data = response(model)
                fields = dict(score=85, eligible=True, reasons=['本科申请通知'])
                fields.update(changes)
                data['choices'][0]['message']['content'] = json.dumps(fields, ensure_ascii=False)
                return FakeResponse(data)
            with self.subTest(changes=changes), patch.object(scoring, 'urlopen', side_effect=remote):
                with self.assertRaisesRegex(ValueError, '^西湖双模型评分失败，请稍后重试$'):
                    scoring.score_pair(ITEM, PROFILE)


if __name__ == '__main__':
    unittest.main()
