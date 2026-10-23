#!/usr/bin/env python3
"""ReAct learning lab. Python 3.9+, standard library only; see 教程.md."""
import argparse
import collections
import csv
import datetime
import difflib
import hashlib
import json
import os
from pathlib import Path
import random
import re
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
VERSION = '1.0'
HOTPOT_URL = 'https://curtis.ml.cmu.edu/datasets/hotpot/hotpot_dev_distractor_v1.json'
REACT_DATA_URL = 'https://raw.githubusercontent.com/ysymyth/ReAct/6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9/data/hotpot_dev_v1_simplified.json'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_env(path):
    """Small dotenv subset: KEY=value, optional quotes; never execute shell code."""
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            raise ValueError('Invalid .env line (expected KEY=value)')
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def normalize(text):
    text = ''.join(c for c in text.lower() if c not in string.punctuation)
    return ' '.join(re.sub(r'\b(a|an|the)\b', ' ', text).split())


def score(prediction, gold):
    p, g = normalize(prediction), normalize(gold)
    em = float(p == g)
    if (p in ('yes', 'no', 'noanswer') or g in ('yes', 'no', 'noanswer')) and p != g:
        return em, 0.0
    common = sum((collections.Counter(p.split()) & collections.Counter(g.split())).values())
    if not common:
        return em, 0.0
    precision, recall = common / len(p.split()), common / len(g.split())
    return em, 2 * precision * recall / (precision + recall)


def request_json(url, payload=None, headers=None, timeout=60, retries=2):
    data = None if payload is None else json.dumps(payload).encode()
    request_headers = {'User-Agent': 'ReActReadingLab/1.0 (educational experiment)',
                       'Accept': 'application/json'}
    if data is not None:
        request_headers['Content-Type'] = 'application/json'
    request_headers.update(headers or {})
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=request_headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # Do not print response bodies: a provider can echo credential-bearing input.
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == retries:
                raise RuntimeError('HTTP %s: check endpoint, model, parameters, quota or access' % exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == retries:
                raise RuntimeError('Network request failed after retries; check connectivity/proxy') from None
        time.sleep(2 ** attempt)


class ChatClient:
    def __init__(self, args):
        self.model = os.environ.get('LLM_MODEL', '')
        self.base_url = os.environ.get('LLM_BASE_URL', '').rstrip('/')
        self.key = os.environ.get('LLM_API_KEY', '')
        if not self.model or not self.base_url or not self.key:
            raise ValueError('Set LLM_MODEL, LLM_BASE_URL, LLM_API_KEY in .env first')
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('LLM_BASE_URL must be a clean http(s) API base URL without credentials/query')
        if parsed.scheme != 'https' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('Remote model endpoints require HTTPS')
        self.temperature = None if os.environ.get('LLM_TEMPERATURE', '0').lower() == 'omit' else float(os.environ.get('LLM_TEMPERATURE', '0'))
        self.token_field = os.environ.get('LLM_TOKEN_FIELD', 'max_completion_tokens')
        if self.token_field not in ('max_tokens', 'max_completion_tokens'):
            raise ValueError('LLM_TOKEN_FIELD must be max_tokens or max_completion_tokens')
        self.thinking = os.environ.get('LLM_THINKING', '').strip()
        if self.thinking not in ('', 'enabled', 'disabled'):
            raise ValueError('LLM_THINKING must be enabled, disabled, or empty')
        self.args = args

    def complete(self, messages):
        payload = {'model': self.model, 'messages': messages,
                   self.token_field: self.args.max_tokens}
        if self.thinking:
            payload['thinking'] = {'type': self.thinking}
        if self.temperature is not None:
            payload['temperature'] = self.temperature
        response = request_json(self.base_url + '/chat/completions', payload,
                                {'Authorization': 'Bearer ' + self.key}, self.args.timeout, self.args.retries)
        try:
            choice = response['choices'][0]
            text = choice['message']['content']
            if not isinstance(text, str) or not text.strip():
                raise ValueError()
        except (KeyError, IndexError, TypeError, ValueError):
            raise RuntimeError('Model returned no text content; check model compatibility/output token limit') from None
        return text, {'usage': response.get('usage', {}), 'model': response.get('model'),
                      'finish_reason': choice.get('finish_reason'), 'response_id': response.get('id')}


class ScriptedClient:
    """Deterministic software smoke check, NOT an LLM and NOT an experiment."""
    def __init__(self, method):
        self.method, self.index = method, 0

    def complete(self, messages):
        if self.method in ('direct', 'cot'):
            return 'Final: Lumen', {'mock': True}
        actions = ['Search[Aster Observatory]', 'Lookup[designer]', 'Search[Mira Vale]', 'Finish[Lumen]']
        action = actions[min(self.index, len(actions) - 1)]
        self.index += 1
        prefix = 'Thought: Follow the next evidence link.\n' if self.method == 'react' else ''
        return prefix + 'Action: ' + action, {'mock': True}


class LocalStore:
    def __init__(self, documents):
        self.documents = documents

    def search(self, entity):
        for title, text in self.documents.items():
            if title.casefold() == entity.casefold():
                return title, text, None
        titles = list(self.documents)
        suggestions = [t for t in titles if entity.casefold() in t.casefold()][:5]
        suggestions = suggestions or difflib.get_close_matches(entity, titles, n=5, cutoff=0.2)
        return None, '', 'Entity not found. Similar titles: ' + json.dumps(suggestions)


class WikiStore:
    def __init__(self, cache, timeout, retries):
        self.cache, self.timeout, self.retries = Path(cache), timeout, retries
        self.cache.mkdir(parents=True, exist_ok=True)

    def query(self, params):
        url = 'https://en.wikipedia.org/w/api.php?' + urllib.parse.urlencode(dict(format='json', formatversion=2, **params))
        path = self.cache / (hashlib.sha256(url.encode()).hexdigest() + '.json')
        if path.exists():
            return read_json(path)
        response = request_json(url, timeout=self.timeout, retries=self.retries)
        if 'error' in response:
            raise RuntimeError('Wikipedia API error')
        write_json(path, {'requested_url': url, 'fetched_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          'response': response})
        return read_json(path)

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


def sentences(text):
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+|\n+', text) if s.strip()]


class Environment:
    def __init__(self, store, obs_chars=4000):
        self.store, self.obs_chars = store, obs_chars
        self.title, self.page, self.keyword, self.matches, self.cursor = '', '', None, [], 0

    def step(self, name, value):
        if name == 'finish':
            return 'Finished.', True
        if name == 'search':
            title, text, error = self.store.search(value)
            self.title, self.page = title or '', text
            self.keyword, self.matches, self.cursor = None, [], 0
            obs = error if error else 'Title: ' + title + '\n' + ' '.join(sentences(text)[:5])
        else:
            if not self.page:
                return 'No current page. Use Search first.', False
            if self.keyword != value.casefold():
                self.keyword, self.cursor = value.casefold(), 0
                self.matches = [s for s in sentences(self.page) if self.keyword in s.casefold()]
            if self.cursor >= len(self.matches):
                obs = 'No more results.'
            else:
                obs = '(Result %s / %s) %s' % (self.cursor + 1, len(self.matches), self.matches[self.cursor])
                self.cursor += 1
        return obs[:self.obs_chars], False


def parse_action(text):
    # Exactly one action; model-generated observations are never executed or trusted.
    if re.search(r'^\s*Observation(?:\s+\d+)?\s*:', text, re.M | re.I):
        raise ValueError('Do not generate Observation; the environment provides it')
    matches = re.findall(r'^\s*Action(?:\s+\d+)?\s*:\s*(Search|Lookup|Finish)\[([^\n\[\]]*)\]\s*$', text, re.M | re.I)
    if len(matches) != 1 or not matches[0][1].strip():
        raise ValueError('Return exactly one Action: Search[entity], Lookup[keyword], or Finish[answer]')
    return matches[0][0].lower(), matches[0][1].strip()


def parse_final(text):
    matches = re.findall(r'^\s*Final\s*:\s*(.+?)\s*$', text, re.M | re.I)
    if len(matches) != 1:
        raise ValueError('Return exactly one Final: short answer line')
    return matches[0].strip()


def make_messages(method, question):
    prompts = read_json(ROOT / 'prompts.json')
    return [{'role': 'system', 'content': prompts[method]},
            {'role': 'user', 'content': 'Question: ' + question}]


def solve(client, method, question, env, args):
    messages = make_messages(method, question)
    trace, answer, status = [], '', 'max_steps'
    max_calls = 1 if method in ('direct', 'cot') else args.max_steps
    for step in range(1, max_calls + 1):
        event = {'step': step}
        try:
            text, metadata = client.complete(messages)
        except Exception as exc:
            event['error'] = str(exc)
            trace.append(event)
            status = 'api_error'
            break
        event.update(raw_output=text, **metadata)
        # Keep raw output in the audit trace; admit it to context only after validation.
        try:
            if method in ('direct', 'cot'):
                answer = parse_final(text)
                status = 'finished'
                trace.append(event)
                break
            name, value = parse_action(text)
            if method == 'react' and not re.search(r'^\s*Thought(?:\s+\d+)?\s*:', text, re.M | re.I):
                raise ValueError('ReAct requires a brief Thought line before Action')
            if method == 'act' and re.search(r'^\s*Thought(?:\s+\d+)?\s*:', text, re.M | re.I):
                raise ValueError('Act-only must return Action without Thought')
            event['action'] = {'name': name, 'argument': value}
        except ValueError as exc:
            event['format_error'] = str(exc)
            event['accepted_to_context'] = False
            trace.append(event)
            if method in ('direct', 'cot'):
                status = 'format_error'
                break
            messages.append({'role': 'user', 'content': 'Format error: ' + str(exc)})
            continue
        event['accepted_to_context'] = True
        messages.append({'role': 'assistant', 'content': text})
        try:
            observation, done = env.step(name, value)
        except Exception as exc:
            event['error'] = str(exc)
            trace.append(event)
            status = 'tool_error'
            break
        event['observation'] = observation
        event['page_title'] = env.title
        trace.append(event)
        if args.verbose:
            print(text + '\nObservation: ' + observation + '\n', flush=True)
        if done:
            answer, status = value, 'finished'
            break
        messages.append({'role': 'user', 'content': 'Observation: ' + observation})
    return {'prediction': answer, 'status': status, 'trace': trace,
            'initial_messages': messages[:2], 'llm_calls': len(trace),
            'tool_calls': sum(e.get('action', {}).get('name') in ('search', 'lookup') for e in trace),
            'format_errors': sum('format_error' in e for e in trace),
            'prompt_tokens': sum(e.get('usage', {}).get('prompt_tokens', 0) for e in trace),
            'completion_tokens': sum(e.get('usage', {}).get('completion_tokens', 0) for e in trace)}


def load_items(path):
    data = read_json(path)
    if not isinstance(data, list) or not data:
        raise ValueError('Dataset must be a nonempty JSON list')
    ids = set()
    for item in data:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k] for k in ('_id', 'question', 'answer')):
            raise ValueError('Each item needs nonempty string _id, question, answer')
        if item['_id'] in ids:
            raise ValueError('Duplicate question ID')
        ids.add(item['_id'])
    return data


def select_items(data, seed, offset, limit):
    order = list(data)
    random.Random(seed).shuffle(order)
    selected = order[offset:offset + limit]
    if not selected:
        raise ValueError('Selection is empty; check offset/limit')
    return selected


def report(out):
    rows = []
    for path in sorted((out / 'records').glob('*.json')):
        row = read_json(path)
        if row.get('schema') == VERSION:
            rows.append(row)
    groups = collections.defaultdict(list)
    for row in rows:
        groups[row['method']].append(row)
    fields = ['method', 'id', 'status', 'prediction', 'gold', 'em', 'f1', 'llm_calls', 'tool_calls',
              'format_errors', 'seconds', 'prompt_tokens', 'completion_tokens']
    with (out / 'results.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    manifest = read_json(out / 'manifest.json')
    lines = ['# 实验汇总', '', '**MOCK 软件测试：不能作为模型实验结果。**' if manifest['mock'] else '真实模型运行记录。',
             '', '答案指标为 0–1；失败题按 0 分计入分母。仅计算答案 EM/F1，不计算 supporting facts/joint 指标。',
             '', '| 方法 | 已运行/计划 | EM | F1 | 平均工具调用 | 异常/未完成 |', '| --- | --- | --- | --- | --- | --- |']
    summary = {}
    for method, group in sorted(groups.items()):
        n = len(group)
        avg = lambda key: sum(r[key] for r in group) / n
        failed = sum(r['status'] != 'finished' for r in group)
        summary[method] = {'n': n, 'em': avg('em'), 'f1': avg('f1'), 'failed': failed}
        lines.append('| %s | %s/%s | %.3f | %.3f | %.2f | %s |' %
                     (method, n, len(manifest['question_ids']), avg('em'), avg('f1'), avg('tool_calls'), failed))
    lines += ['', '各组尚未跑完同一批题目时，不进行方法优劣比较。', '',
              '完整轨迹见 records/；逐题表格见 results.csv；配置和题目顺序见 manifest.json。',
              'prompt_tokens/completion_tokens 来自服务返回，缺失时按 0 汇总，不代表免费；没有推算费用。', '']
    (out / 'summary.md').write_text('\n'.join(lines), encoding='utf-8')
    write_json(out / 'summary.json', summary)
    print('\n'.join(lines))


def run(args):
    data_path = Path(args.data).resolve()
    data = load_items(data_path)
    selected = select_items(data, args.seed, args.offset, args.limit)
    if args.mock and (args.env != 'local' or len(selected) != 1 or selected[0]['_id'] != 'toy-aster'):
        raise ValueError('--mock is only valid for --data data/toy.json --env local --limit 1 --offset 0')
    docs = read_json(args.corpus) if args.env == 'local' else None
    if args.env == 'local' and not all(isinstance(k, str) and isinstance(v, str) for k, v in docs.items()):
        raise ValueError('Corpus must be a JSON title -> text object')
    client = None if args.mock else ChatClient(args)
    # Save only explicitly selected non-secret settings.
    config = {'schema': VERSION, 'mock': args.mock, 'environment': args.env, 'seed': args.seed,
              'offset': args.offset, 'limit': args.limit, 'max_steps': args.max_steps,
              'max_tokens': args.max_tokens, 'timeout': args.timeout, 'retries': args.retries,
              'obs_chars': args.obs_chars, 'methods': args.methods,
              'dataset_sha256': hashlib.sha256(data_path.read_bytes()).hexdigest(),
              'corpus_sha256': digest(docs) if docs is not None else None,
              'prompts': read_json(ROOT / 'prompts.json'),
              'code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'question_ids': [q['_id'] for q in selected],
              'model': 'scripted-mock' if args.mock else client.model,
              'base_url': None if args.mock else client.base_url,
              'temperature': None if args.mock else client.temperature,
              'token_field': None if args.mock else client.token_field,
              'thinking': None if args.mock else client.thinking}
    out = Path(args.out).resolve()
    manifest_path = out / 'manifest.json'
    config_hash = digest(config)
    if manifest_path.exists():
        if not args.resume:
            raise ValueError('Output directory already has a run; use a new --out or --resume')
        if read_json(manifest_path)['config_hash'] != config_hash:
            raise ValueError('Configuration/data/code/prompt changed; use a new --out')
    else:
        if args.resume:
            raise ValueError('--resume requires an existing manifest')
        if out.exists() and any(out.iterdir()):
            raise ValueError('Output directory is not empty')
        write_json(manifest_path, dict(config, config_hash=config_hash,
                                     started_at=datetime.datetime.now(datetime.timezone.utc).isoformat()))
    records = out / 'records'
    records.mkdir(exist_ok=True)
    store = LocalStore(docs) if args.env == 'local' else WikiStore(out / 'wiki_cache', args.timeout, args.retries)
    try:
        for item in selected:
            for method in args.methods:
                path = records / (method + '-' + hashlib.sha256(item['_id'].encode()).hexdigest()[:20] + '.json')
                if path.exists():
                    continue
                started = time.monotonic()
                result = solve(ScriptedClient(method) if args.mock else client, method, item['question'],
                               Environment(store, args.obs_chars), args)
                # Gold is added only after model/tool interaction has ended.
                em, f1 = score(result['prediction'], item['answer']) if result['status'] == 'finished' else (0., 0.)
                row = dict(result, schema=VERSION, mock=args.mock, method=method, id=item['_id'],
                           question=item['question'], gold=item['answer'], em=em, f1=f1,
                           seconds=round(time.monotonic() - started, 3))
                write_json(path, row)
                print('%s %s status=%s EM=%s answer=%s' % (method, item['_id'], row['status'], em, row['prediction']), flush=True)
                # Credentials/parameter errors usually affect every question. Stop early, preserve evidence.
                if row['status'] == 'api_error':
                    raise RuntimeError('Model request failed. Inspect the record and fix configuration; use a NEW --out for a rerun')
    finally:
        report(out)


def prepare(args):
    data = load_items(args.source)
    chosen = select_items(data, args.seed, args.offset, args.limit)
    # Drop context and supporting facts, so evaluation evidence is never in model input.
    subset = [{k: q[k] for k in ('_id', 'question', 'answer')} for q in chosen]
    dest = Path(args.dest)
    if dest.exists():
        raise ValueError('Destination exists; choose a new path')
    write_json(dest, subset)
    write_json(str(dest) + '.meta.json', {'source': str(Path(args.source).resolve()),
               'source_sha256': hashlib.sha256(Path(args.source).read_bytes()).hexdigest(),
               'seed': args.seed, 'offset': args.offset, 'limit': args.limit,
               'ids': [q['_id'] for q in subset], 'license': 'HotpotQA: CC BY-SA 4.0 (when using official data)'})
    print('Prepared %s questions -> %s' % (len(subset), dest))


def download(args):
    url = REACT_DATA_URL if args.source == 'react' else HOTPOT_URL
    dest = Path(args.dest or ('data/hotpot_dev_react.json' if args.source == 'react' else 'data/hotpot_dev_distractor_v1.json'))
    if dest.exists():
        raise ValueError('Destination already exists')
    data = request_json(url, timeout=60, retries=2)
    source_hash = digest(data)
    if not isinstance(data, list) or not data:
        raise ValueError('Downloaded dataset is not a nonempty list')
    if args.source == 'react':
        # Author-released file omits original HotpotQA IDs: preserve original row index explicitly.
        data = [dict(item, _id='react-dev-%05d' % i, source_index=i) for i, item in enumerate(data)]
    write_json(dest, data)
    try:
        load_items(dest)
    except Exception:
        dest.unlink()
        raise
    write_json(str(dest) + '.source.json', {'source_url': url, 'source_content_hash': source_hash,
               'downloaded_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'license': 'HotpotQA CC BY-SA 4.0',
               'transformation': 'Added react-dev-index IDs; IDs are not original HotpotQA IDs' if args.source == 'react' else 'No field transformation'})
    print('Downloaded:', dest, 'questions:', len(data), 'sha256:', hashlib.sha256(dest.read_bytes()).hexdigest())


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError('must be nonnegative')
    return number


def main():
    os.chdir(ROOT)
    load_env(ROOT / '.env')
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    p = subs.add_parser('run', help='Run paired baselines and ReAct')
    p.add_argument('--data', default='data/toy.json')
    p.add_argument('--corpus', default='data/toy_corpus.json')
    p.add_argument('--env', choices=['local', 'wiki'], default='local')
    p.add_argument('--methods', nargs='+', choices=['direct', 'cot', 'react', 'act'], default=['direct', 'cot', 'react'])
    p.add_argument('--limit', type=positive, default=1)
    p.add_argument('--offset', type=nonnegative, default=0)
    p.add_argument('--seed', type=int, default=233)
    p.add_argument('--max-steps', type=positive, default=7)
    p.add_argument('--max-tokens', type=positive, default=1024)
    p.add_argument('--obs-chars', type=positive, default=4000)
    p.add_argument('--timeout', type=positive, default=60)
    p.add_argument('--retries', type=nonnegative, default=2)
    p.add_argument('--out', required=True)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--mock', action='store_true')
    p.add_argument('--verbose', action='store_true')
    p.set_defaults(func=run)
    p = subs.add_parser('prepare', help='Freeze a question subset; strip context from official data')
    p.add_argument('--source', required=True)
    p.add_argument('--dest', required=True)
    p.add_argument('--limit', type=positive, default=50)
    p.add_argument('--offset', type=nonnegative, default=0)
    p.add_argument('--seed', type=int, default=233)
    p.set_defaults(func=prepare)
    p = subs.add_parser('download', help='Download author-released or original HotpotQA development data')
    p.add_argument('--source', choices=['react', 'hotpot'], default='react')
    p.add_argument('--dest', default=None)
    p.set_defaults(func=download)
    p = subs.add_parser('report', help='Regenerate CSV and Markdown from saved records')
    p.add_argument('--out', required=True)
    p.set_defaults(func=lambda a: report(Path(a.out)))
    args = parser.parse_args()
    if hasattr(args, 'methods') and len(set(args.methods)) != len(args.methods):
        parser.error('Do not repeat methods')
    try:
        args.func(args)
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print('Error:', exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nInterrupted. Saved records remain; resume with the same command plus --resume.', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
