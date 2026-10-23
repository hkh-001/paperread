"""Serial, bounded Wikipedia retrieval with auditable snapshots. No model API calls."""
import datetime
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
from pathlib import Path
import random
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


def retry_after(value, now=None):
    """HTTP Retry-After: nonnegative integer seconds or an HTTP date."""
    if not value:
        return None
    try:
        if str(value).strip().isdigit():
            seconds = float(value)
            return seconds if math.isfinite(seconds) else None
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=datetime.timezone.utc)
        return max(0., date.timestamp() - (time.time() if now is None else now))
    except (TypeError, ValueError, OverflowError):
        return None


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        json.dump(value, f, ensure_ascii=False, indent=2)
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


class Transport:
    def __init__(self, timeout=30, retries=3, interval=1.5, budget=90, emit=None,
                 clock=time.monotonic, sleep=time.sleep, wall=time.time, jitter=None):
        if timeout <= 0 or retries < 0 or interval < 0 or budget <= 0:
            raise ValueError('Invalid retrieval limits')
        self.timeout, self.retries, self.interval, self.budget = timeout, retries, interval, budget
        self.emit = emit or (lambda e: None)
        self.clock, self.sleep, self.wall = clock, sleep, wall
        self.jitter = jitter or (lambda: random.uniform(0, 1))
        self.last_start = None
        self.not_before = 0.

    def get(self, url):
        deadline = self.clock() + self.budget
        def wait(seconds, reason):
            if seconds <= 0:
                return
            if seconds >= deadline - self.clock():
                self.emit({'kind': 'budget_exhausted', 'required_wait': round(seconds, 3), 'reason': reason})
                raise RuntimeError('Wikipedia retry budget exhausted; request not retried early')
            self.emit({'kind': 'wait', 'seconds': round(seconds, 3), 'reason': reason})
            # Short sleeps let callers interrupt even when Retry-After is long.
            end = self.clock() + seconds
            while self.clock() < end:
                self.sleep(min(1., end - self.clock()))
        for attempt in range(self.retries + 1):
            wait(max(0., self.not_before - self.clock()), 'server_cooldown')
            if self.last_start is not None:
                wait(max(0., self.interval - (self.clock() - self.last_start)), 'pacing')
            remaining = deadline - self.clock()
            if remaining <= 0:
                self.emit({'kind': 'budget_exhausted'})
                raise RuntimeError('Wikipedia request budget exhausted')
            self.last_start = self.clock()
            code, delay, error = None, None, None
            try:
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'PaperReadReActLab/1.1 (https://github.com/hkh-001/paperread)',
                    'Accept': 'application/json'})
                with urllib.request.urlopen(req, timeout=min(self.timeout, remaining)) as response:
                    payload = json.load(response)
                    delay = retry_after(response.headers.get('Retry-After'), self.wall())
                if not isinstance(payload, dict):
                    raise ValueError('Invalid Wikipedia response')
                api_error = payload.get('error')
                if api_error:
                    code = api_error.get('code', 'unknown') if isinstance(api_error, dict) else 'unknown'
                    if code not in ('ratelimited', 'maxlag', 'readonly'):
                        self.emit({'kind': 'attempt', 'attempt': attempt + 1, 'outcome': 'api_error', 'code': code})
                        raise RuntimeError('Wikipedia API rejected the query')
                    error = 'api_error'
                else:
                    self.emit({'kind': 'attempt', 'attempt': attempt + 1, 'outcome': 'success'})
                    return payload
            except urllib.error.HTTPError as exc:
                code = exc.code
                delay = retry_after(exc.headers.get('Retry-After'), self.wall()) if exc.headers else None
                exc.close()
                if code not in (408, 429, 500, 502, 503, 504):
                    self.emit({'kind': 'attempt', 'attempt': attempt + 1, 'outcome': 'http_error', 'code': code})
                    raise RuntimeError('Wikipedia HTTP %s (not retryable)' % code) from None
                error = 'http_error'
            except (urllib.error.URLError, TimeoutError, OSError):
                error = 'network_error'
            except (ValueError, UnicodeError):
                error = 'invalid_json'
            self.emit({'kind': 'attempt', 'attempt': attempt + 1, 'outcome': error,
                       'code': code, 'retry_after': delay})
            backoff = min(30., 2. ** (attempt + 1)) + self.jitter()
            # Preserve host cooldown even if this query runs out of retries/budget.
            self.not_before = self.clock() + max(backoff, delay or 0.)
            if attempt == self.retries:
                self.emit({'kind': 'retries_exhausted'})
                raise RuntimeError('Wikipedia retrieval failed after %s attempts (%s %s)' %
                                   (attempt + 1, error, code or ''))
            # Never shorten the server's requested delay to fit our budget.
            wait(max(backoff, delay or 0.), 'retry_after' if delay is not None else 'backoff')



class WikiStore:
    def __init__(self, cache, timeout=30, retries=3, shared_cache=None, interval=1.5,
                 budget=90, ttl=604800, audit=None):
        self.cache = Path(cache)
        self.shared = Path(shared_cache) if shared_cache else None
        self.ttl, self.audit, self.events = ttl, Path(audit) if audit else None, []
        self.cache.mkdir(parents=True, exist_ok=True)
        self.transport = Transport(timeout, retries, interval, budget, emit=self._record)
        self.active_url = None

    def _record(self, event):
        event = dict(event, url=self.active_url,
                     timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat())
        self.events.append(event)
        if self.audit:
            self.audit.parent.mkdir(parents=True, exist_ok=True)
            with self.audit.open('a', encoding='utf-8') as f:
                f.write(json.dumps(event, ensure_ascii=False) + '\n')

    def _load(self, path, url, shared=False):
        if not path.exists():
            return None
        try:
            obj = json.loads(path.read_text(encoding='utf-8'))
            assert obj['requested_url'] == url
            assert isinstance(obj['response'], dict) and 'error' not in obj['response']
            stamp = datetime.datetime.fromisoformat(obj['fetched_at'])
            assert stamp.tzinfo is not None
            age = time.time() - stamp.timestamp()
            if shared and (self.ttl == 0 or age > self.ttl or age < -60):
                self._record({'kind': 'cache_expired', 'tier': 'shared'})
                return None
            return obj
        except (ValueError, KeyError, TypeError, AssertionError, OSError):
            self._record({'kind': 'cache_invalid', 'tier': 'shared' if shared else 'run'})
            return None

    def query(self, params):
        params = dict(format='json', formatversion=2, maxlag=5, **params)
        url = 'https://en.wikipedia.org/w/api.php?' + urllib.parse.urlencode(sorted(params.items()))
        self.active_url = url
        name = hashlib.sha256(url.encode()).hexdigest() + '.json'
        path = self.cache / name
        obj = self._load(path, url)
        if obj is not None:
            self._record({'kind': 'cache_hit', 'tier': 'run', 'fetched_at': obj['fetched_at']})
            return obj
        obj = self._load(self.shared / name, url, True) if self.shared else None
        if obj is not None:
            atomic_json(path, obj)
            self._record({'kind': 'cache_hit', 'tier': 'shared', 'fetched_at': obj['fetched_at']})
            return obj
        self._record({'kind': 'cache_miss'})
        response = self.transport.get(url)
        obj = {'requested_url': url, 'fetched_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'response': response}
        atomic_json(path, obj)
        if self.shared:
            atomic_json(self.shared / name, obj)
        self._record({'kind': 'cache_saved', 'fetched_at': obj['fetched_at']})
        return obj

    def search(self, entity):
        result = self.query({'action': 'query', 'prop': 'extracts|info', 'inprop': 'url',
                             'explaintext': 1, 'redirects': 1, 'titles': entity})['response']
        pages = result.get('query', {}).get('pages', [])
        if pages and 'missing' not in pages[0] and pages[0].get('extract'):
            page = pages[0]
            return page['title'], page['extract'], None
        result = self.query({'action': 'query', 'list': 'search', 'srsearch': entity, 'srlimit': 5})['response']
        titles = [r['title'] for r in result.get('query', {}).get('search', [])]
        return None, '', 'Entity not found. Similar titles: ' + json.dumps(titles)
