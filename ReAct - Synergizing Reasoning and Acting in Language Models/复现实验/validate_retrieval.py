#!/usr/bin/env python3
"""Replay prior tool failures without calling any LLM; writes a new evidence directory."""
import argparse
import datetime
import json
from pathlib import Path
import sys
from retrieval import WikiStore, atomic_json

ROOT = Path(__file__).resolve().parent
QUERIES = ['Stuart Bird film director', 'Stuart Baird filmography', 'Wendy Schaal']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if out.exists():
        parser.error('Output path exists; use a new directory')
    out.mkdir(parents=True)
    shared = ROOT / 'cache/wiki'
    # Empty run snapshots; existing shared entries are logged if reused.
    cold = WikiStore(out/'cold/wiki_cache', shared_cache=shared, audit=out/'cold/retrieval.jsonl')
    results=[]
    for query in QUERIES:
        try:
            title,text,message = cold.search(query)
            result={'query':query,'transport_ok':True,'matched_title':title,
                    'text_length':len(text),'candidate_message':message}
        except RuntimeError as exc:
            result={'query':query,'transport_ok':False,'error':str(exc)}
        results.append(result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    warm=WikiStore(out/'warm/wiki_cache', shared_cache=shared, audit=out/'warm/retrieval.jsonl')
    def forbidden_network(url):
        raise AssertionError('Second session unexpectedly needed the network')
    warm.transport.get=forbidden_network
    warm_results=[]
    for result in results:
        if not result['transport_ok']:
            continue
        try:
            warm.search(result['query'])
            warm_results.append({'query':result['query'],'cache_only_ok':True})
        except Exception as exc:
            warm_results.append({'query':result['query'],'cache_only_ok':False,'error':str(exc)})
    summary={'date':datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'no_model_calls':True,'queries':results,'second_session':warm_results,
             'cold_attempts':sum(e['kind']=='attempt' for e in cold.events),
             'cold_429':sum(e.get('code')==429 for e in cold.events),
             'cold_wait_seconds':sum(e.get('seconds',0) for e in cold.events if e['kind']=='wait'),
             'second_session_network_attempts':sum(e['kind']=='attempt' for e in warm.events),
             'shared_cache_hits':sum(e['kind']=='cache_hit' and e.get('tier')=='shared' for e in warm.events)}
    atomic_json(out/'validation.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return 0 if all(r['transport_ok'] for r in results) and all(r['cache_only_ok'] for r in warm_results) else 1


if __name__=='__main__':
    sys.exit(main())
