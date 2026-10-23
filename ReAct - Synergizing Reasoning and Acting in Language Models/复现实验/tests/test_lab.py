import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, HTTPServer
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import react_lab as lab


def args(**overrides):
    d = dict(max_tokens=1024, max_steps=7, timeout=5, retries=0, verbose=False)
    d.update(overrides)
    return argparse.Namespace(**d)


class LabTests(unittest.TestCase):
    def test_scoring(self):
        self.assertEqual(lab.score('The Eiffel Tower!', 'Eiffel Tower'), (1., 1.))
        self.assertEqual(lab.score('yes indeed', 'yes'), (0., 0.))
        self.assertEqual(lab.score('wrong', 'Lumen'), (0., 0.))
        self.assertAlmostEqual(lab.score('New York', 'York')[1], 2 / 3)

    def test_parse_rejects_hallucinated_observation_and_multi_action(self):
        self.assertEqual(lab.parse_action('Thought 1: Find it.\nAction 1: Search[Aster]'), ('search', 'Aster'))
        for text in ['Action: Search[A]\nObservation: invented', 'Action: Search[A]\nAction: Finish[B]', 'Action: Finish[]']:
            with self.assertRaises(ValueError):
                lab.parse_action(text)

    def test_lookup_advances_and_resets(self):
        env = lab.Environment(lab.LocalStore({'One': 'Key first. Key second.', 'Two': 'Elsewhere.'}))
        env.step('search', 'One')
        self.assertIn('Key first', env.step('lookup', 'key')[0])
        self.assertIn('Key second', env.step('lookup', 'key')[0])
        self.assertEqual(env.step('lookup', 'key')[0], 'No more results.')
        env.step('search', 'Two')
        self.assertEqual(env.step('lookup', 'key')[0], 'No more results.')
        env.step('search', 'Missing')
        self.assertEqual(env.page, '')

    def test_mock_cycle_uses_real_local_tool(self):
        docs = lab.read_json(lab.ROOT / 'data/toy_corpus.json')
        result = lab.solve(lab.ScriptedClient('react'), 'react', 'test', lab.Environment(lab.LocalStore(docs)), args())
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['prediction'], 'Lumen')
        self.assertEqual(result['tool_calls'], 3)
        self.assertIn('Mira Vale', result['trace'][1]['observation'])
        self.assertNotIn('answer', result['initial_messages'][1]['content'])

    def test_budget_and_invalid_format(self):
        class Bad:
            def complete(self, messages):
                return 'I invented an answer', {}
        result = lab.solve(Bad(), 'react', 'x', lab.Environment(lab.LocalStore({})), args(max_steps=2))
        self.assertEqual(result['llm_calls'], 2)
        self.assertEqual(result['status'], 'max_steps')
        self.assertEqual(result['prediction'], '')
        self.assertEqual(result['format_errors'], 2)

    def test_api_failure_preserves_trace(self):
        class Bad:
            def complete(self, messages):
                raise RuntimeError('offline')
        result = lab.solve(Bad(), 'react', 'x', lab.Environment(lab.LocalStore({})), args())
        self.assertEqual(result['status'], 'api_error')
        self.assertEqual(result['trace'][0]['error'], 'offline')

    def test_subset_disjoint(self):
        data = [{'_id': str(i)} for i in range(100)]
        dev = lab.select_items(data, 233, 0, 10)
        evaluation = lab.select_items(data, 233, 10, 50)
        self.assertFalse(set(d['_id'] for d in dev) & set(d['_id'] for d in evaluation))

    def test_wiki_cache_and_exact_title(self):
        payload = {'query': {'pages': [{'title': 'Example', 'extract': 'First. Second.'}]}}
        with tempfile.TemporaryDirectory() as tmp:
            store = lab.WikiStore(tmp, 3, 0)
            with patch.object(lab, 'request_json', return_value=payload) as request:
                self.assertEqual(store.search('Example')[:2], ('Example', 'First. Second.'))
                store.search('Example')
                self.assertEqual(request.call_count, 1)

    def test_end_to_end_records_resume_guard_and_prepare(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'run'
            config = args(data=str(lab.ROOT / 'data/toy.json'), corpus=str(lab.ROOT / 'data/toy_corpus.json'),
                          env='local', seed=233, offset=0, limit=1, methods=['direct', 'cot', 'react'],
                          mock=True, obs_chars=4000, out=str(out), resume=False)
            with contextlib.redirect_stdout(io.StringIO()):
                lab.run(config)
                paths = list((out / 'records').glob('*.json'))
                self.assertEqual(len(paths), 3)
                times = {p.name: p.stat().st_mtime_ns for p in paths}
                config.resume = True
                lab.run(config)
                self.assertEqual(times, {p.name: p.stat().st_mtime_ns for p in paths})
                config.max_steps = 9
                with self.assertRaises(ValueError):
                    lab.run(config)
                self.assertTrue((out / 'results.csv').exists())
                self.assertIn('MOCK', (out / 'summary.md').read_text())
                src, dest = Path(tmp) / 'source.json', Path(tmp) / 'subset.json'
                lab.write_json(src, [{'_id': 'id', 'question': 'q', 'answer': 'gold',
                                      'context': [['secret evidence']], 'supporting_facts': [['secret', 0]]}])
                lab.prepare(args(source=str(src), dest=str(dest), seed=233, offset=0, limit=1))
                self.assertEqual(set(lab.read_json(dest)[0]), {'_id', 'question', 'answer'})

    def test_real_http_adapter_protocol_without_paid_api(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'model': 'local-test', 'choices': [{'message': {'content': 'Final: Lumen'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10}}).encode())
            def log_message(self, *a):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict('os.environ', {'LLM_BASE_URL': 'http://127.0.0.1:%s/v1' % server.server_port,
                            'LLM_MODEL': 'local-test', 'LLM_API_KEY': 'not-a-real-key',
                            'LLM_TOKEN_FIELD': 'max_completion_tokens', 'LLM_TEMPERATURE': 'omit'}):
                client = lab.ChatClient(args())
                output, meta = client.complete([{'role': 'user', 'content': 'hello'}])
            self.assertEqual(output, 'Final: Lumen')
            self.assertEqual(received[0][0], '/v1/chat/completions')
            self.assertNotIn('temperature', received[0][1])
            self.assertEqual(received[0][1]['max_completion_tokens'], 1024)
            self.assertEqual(meta['usage']['prompt_tokens'], 10)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
