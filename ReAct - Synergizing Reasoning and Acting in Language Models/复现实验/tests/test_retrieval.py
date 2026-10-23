import datetime
from email.utils import format_datetime
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import retrieval
import react_lab as lab


class Clock:
    def __init__(self):
        self.now, self.sleeps = 0., []
    def clock(self):
        return self.now
    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Response(io.BytesIO):
    def __init__(self, payload, headers=None):
        super().__init__(json.dumps(payload).encode())
        self.headers = headers or {}


def error(code, header=None):
    return urllib.error.HTTPError('https://example.test', code, 'test', header or {}, io.BytesIO())


class RetrievalTests(unittest.TestCase):
    def transport(self, **kwargs):
        self.clock, self.events = Clock(), []
        return retrieval.Transport(clock=self.clock.clock, sleep=self.clock.sleep, wall=lambda: 1000.,
                                   jitter=lambda: 0., emit=self.events.append, **kwargs)

    def test_retry_after_seconds_date_invalid(self):
        self.assertEqual(retrieval.retry_after('8'), 8)
        date = format_datetime(datetime.datetime.fromtimestamp(1012, datetime.timezone.utc), usegmt=True)
        self.assertEqual(retrieval.retry_after(date, now=1000), 12)
        self.assertEqual(retrieval.retry_after(date, now=1020), 0)
        for value in ['garbage', '-1', 'nan', None]:
            self.assertIsNone(retrieval.retry_after(value))

    def test_429_waits_retry_after_then_recovers(self):
        t = self.transport(retries=3)
        with patch('retrieval.urllib.request.urlopen', side_effect=[error(429, {'Retry-After': '8'}), Response({'ok': 1})]) as call:
            self.assertEqual(t.get('https://example.test'), {'ok': 1})
        self.assertEqual(call.call_count, 2)
        self.assertEqual(sum(self.clock.sleeps), 8.)
        self.assertEqual([e['outcome'] for e in self.events if e['kind']=='attempt'], ['http_error', 'success'])

    def test_http_date_is_honored(self):
        date = format_datetime(datetime.datetime.fromtimestamp(1010, datetime.timezone.utc), usegmt=True)
        t = self.transport()
        with patch('retrieval.urllib.request.urlopen', side_effect=[error(503, {'Retry-After': date}), Response({'ok': 1})]):
            t.get('https://example.test')
        self.assertEqual(sum(self.clock.sleeps), 10.)

    def test_retry_after_over_budget_stops_without_early_retry(self):
        t = self.transport(budget=10)
        with patch('retrieval.urllib.request.urlopen', side_effect=error(429, {'Retry-After': '120'})) as call:
            with self.assertRaisesRegex(RuntimeError, 'budget'):
                t.get('https://example.test')
        self.assertEqual(call.call_count, 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_server_cooldown_survives_failed_query(self):
        t = self.transport(budget=10, retries=0)
        with patch('retrieval.urllib.request.urlopen', side_effect=error(429, {'Retry-After': '120'})) as call:
            with self.assertRaises(RuntimeError):
                t.get('https://example.test/first')
            with self.assertRaisesRegex(RuntimeError, 'budget'):
                t.get('https://example.test/second')
        self.assertEqual(call.call_count, 1)

    def test_persistent_failures_stop_at_limit(self):
        t = self.transport(retries=2)
        with patch('retrieval.urllib.request.urlopen', side_effect=error(429)) as call:
            with self.assertRaisesRegex(RuntimeError, '3 attempts'):
                t.get('https://example.test')
        self.assertEqual(call.call_count, 3)
        self.assertEqual(sum(self.clock.sleeps), 6.)
        self.assertEqual(self.events[-1]['kind'], 'retries_exhausted')

    def test_permanent_http_error_is_not_retried(self):
        t = self.transport()
        with patch('retrieval.urllib.request.urlopen', side_effect=error(403)) as call:
            with self.assertRaisesRegex(RuntimeError, '403'):
                t.get('https://example.test')
        self.assertEqual(call.call_count, 1)
        self.assertFalse(self.clock.sleeps)

    def test_network_and_api_rate_errors_recover(self):
        t = self.transport()
        with patch('retrieval.urllib.request.urlopen', side_effect=[urllib.error.URLError('timeout'),
                   Response({'error': {'code': 'maxlag'}}), Response({'query': {}})]) as call:
            self.assertEqual(t.get('https://example.test'), {'query': {}})
        self.assertEqual(call.call_count, 3)
        self.assertEqual(sum(self.clock.sleeps), 6.)

    def test_successful_requests_are_paced(self):
        t = self.transport(interval=1.5)
        starts=[]
        def respond(*a, **kw):
            starts.append(self.clock.now)
            return Response({'ok': 1})
        with patch('retrieval.urllib.request.urlopen', side_effect=respond):
            t.get('https://example.test/a')
            t.get('https://example.test/b')
        self.assertEqual(starts, [0., 1.5])

    def test_shared_cache_snapshot_ttl_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); shared=base/'shared'
            a=retrieval.WikiStore(base/'a', shared_cache=shared)
            with patch.object(a.transport, 'get', return_value={'query': {'pages': []}}) as get:
                original=a.query({'action': 'query', 'titles': 'Example'})
                self.assertEqual(get.call_count, 1)
            b=retrieval.WikiStore(base/'b', shared_cache=shared)
            with patch.object(b.transport, 'get', side_effect=AssertionError('network must not run')):
                self.assertEqual(b.query({'titles': 'Example', 'action': 'query'}), original)
            self.assertTrue(any(e.get('tier')=='shared' for e in b.events))
            # Expire shared entry, but the existing run snapshot remains fixed.
            path=next(shared.glob('*.json')); stale=json.loads(path.read_text())
            stale['fetched_at']='2000-01-01T00:00:00+00:00'
            path.write_text(json.dumps(stale))
            with patch.object(b.transport, 'get', side_effect=AssertionError('snapshot must not refresh')):
                self.assertEqual(b.query({'action': 'query','titles': 'Example'}), original)
            c=retrieval.WikiStore(base/'c', shared_cache=shared)
            with patch.object(c.transport, 'get', return_value={'refreshed': True}) as get:
                self.assertEqual(c.query({'action': 'query','titles': 'Example'})['response'], {'refreshed': True})
                self.assertEqual(get.call_count,1)
            self.assertTrue(any(e['kind']=='cache_expired' for e in c.events))
            path.write_text('broken')
            d=retrieval.WikiStore(base/'d', shared_cache=shared)
            with patch.object(d.transport, 'get', return_value={'repaired': True}):
                self.assertEqual(d.query({'action': 'query','titles': 'Example'})['response'],{'repaired': True})
            self.assertTrue(any(e['kind']=='cache_invalid' for e in d.events))

    def test_failed_query_not_cached_and_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            store=retrieval.WikiStore(base/'run', shared_cache=base/'shared', audit=base/'audit.jsonl', retries=0)
            with patch('retrieval.urllib.request.urlopen', side_effect=error(429)):
                with self.assertRaises(RuntimeError):
                    store.query({'action': 'query', 'titles': 'X'})
            self.assertFalse(list((base/'run').glob('*.json')))
            self.assertFalse(list((base/'shared').glob('*.json')))
            events=[json.loads(l) for l in (base/'audit.jsonl').read_text().splitlines()]
            self.assertTrue(any(e.get('code')==429 for e in events))
            self.assertEqual(events[-1]['kind'], 'retries_exhausted')

    def test_failure_events_attached_to_question_trace(self):
        class Client:
            def complete(self,messages):
                return 'Thought: Find X.\nAction: Search[X]', {}
        from argparse import Namespace
        with tempfile.TemporaryDirectory() as tmp:
            store=retrieval.WikiStore(tmp, retries=0)
            with patch('retrieval.urllib.request.urlopen', side_effect=error(429)):
                result=lab.solve(Client(),'react','q',lab.Environment(store),Namespace(max_steps=7,verbose=False))
            self.assertEqual(result['status'],'tool_error')
            self.assertTrue(any(e.get('code')==429 for e in result['trace'][0]['retrieval_events']))


if __name__ == '__main__':
    unittest.main()
