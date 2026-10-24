#!/usr/bin/env python3
"""Paired, counterbalanced baseline/state-feedback experiment; standard library only."""
import argparse
import collections
import datetime
import hashlib
import math
from pathlib import Path
import time
import react_lab as lab

ARMS = ('react_baseline', 'react_state')


def mechanism_counts(rows):
    events = [e for r in rows for e in r['trace']]
    return {
        'empty_page_lookups': sum(e.get('action', {}).get('name') == 'lookup'
                                  and e.get('observation', '').startswith('No current page') for e in events),
        'format_errors': sum('format_error' in e for e in events),
        'max_steps': sum(r['status'] == 'max_steps' for r in rows),
        'tool_errors': sum(r['status'] == 'tool_error' for r in rows),
        'llm_calls': sum(r['llm_calls'] for r in rows),
        'tool_calls': sum(r['tool_calls'] for r in rows),
        'prompt_tokens': sum(r['prompt_tokens'] for r in rows),
        'completion_tokens': sum(r['completion_tokens'] for r in rows),
    }


def comparison(out):
    manifest = lab.read_json(out / 'manifest.json')
    rows = [lab.read_json(p) for p in sorted((out/'records').glob('*.json'))]
    groups = {arm: [r for r in rows if r['method'] == arm] for arm in ARMS}
    stats = {a: dict(mechanism_counts(rs), n=len(rs),
                     em=sum(r['em'] for r in rs)/len(rs) if rs else None,
                     f1=sum(r['f1'] for r in rs)/len(rs) if rs else None)
             for a,rs in groups.items()}
    lookup = {a:{r['id']:r for r in rs} for a,rs in groups.items()}
    paired = set(lookup[ARMS[0]]) & set(lookup[ARMS[1]])
    wins = sum(lookup[ARMS[1]][i]['em'] > lookup[ARMS[0]][i]['em'] for i in paired)
    losses = sum(lookup[ARMS[1]][i]['em'] < lookup[ARMS[0]][i]['em'] for i in paired)
    discordant=wins+losses
    p = min(1.,2*sum(math.comb(discordant,k) for k in range(min(wins,losses)+1))/2**discordant) if discordant else 1.
    result = {'complete':all(len(groups[a])==len(manifest['question_ids']) for a in ARMS),
              'groups':stats,'paired_n':len(paired),'state_only_correct':wins,
              'baseline_only_correct':losses,'mcnemar_exact_two_sided_p':p}
    lab.write_json(out/'comparison.json',result)
    return result


def run(args):
    lab.load_env(lab.ROOT/'.env')
    data_path=Path(args.data).resolve()
    data=lab.load_items(data_path)
    excluded=[]
    for path in args.exclude:
        items=lab.load_items(path)
        if {x['_id'] for x in data} & {x['_id'] for x in items}:
            raise ValueError('Dataset overlaps excluded IDs')
        if {x['question'].strip().casefold() for x in data} & {x['question'].strip().casefold() for x in items}:
            raise ValueError('Dataset overlaps excluded question text')
        excluded.append({'file':str(Path(path).resolve()),'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()})
    if args.phase=='evaluation' and not args.exclude:
        raise ValueError('Evaluation requires --exclude previous development/evaluation subsets')
    ordered=lab.select_items(data,args.seed,0,len(data))
    plan=[{'id':q['_id'],'arms':list(ARMS if i%2==0 else reversed(ARMS))} for i,q in enumerate(ordered)]
    client=lab.ChatClient(args)
    config={'schema':lab.VERSION,'mock':False,'environment':'wiki','phase':args.phase,
            'question_ids':[q['_id'] for q in ordered],'methods':list(ARMS),'pair_order':plan,
            'dataset_sha256':hashlib.sha256(data_path.read_bytes()).hexdigest(),'excluded':excluded,
            'model':client.model,'base_url':client.base_url,'temperature':client.temperature,
            'thinking':client.thinking,'token_field':client.token_field,
            'seed':args.seed,'max_steps':args.max_steps,'max_tokens':args.max_tokens,'obs_chars':args.obs_chars,
            'timeout':args.timeout,'retries':args.retries,'wiki_interval':1.5,'wiki_retries':3,'wiki_budget':90.,
            'prompts':lab.read_json(lab.ROOT/'prompts.json'),
            'state_feedback_example':lab.page_state_feedback(lab.Environment(lab.LocalStore({}))),
            'source_sha256':{name:hashlib.sha256((lab.ROOT/name).read_bytes()).hexdigest()
                              for name in ['react_lab.py','retrieval.py','prompts.json',Path(__file__).name]},
            'primary_metric':'Count of executed Lookup actions returning No current page',
            'secondary_metrics':['EM','F1','max_steps','format_errors','llm_calls','tokens'],
            'cache_policy':'Same read-through Wikipedia snapshots in this run; no gold/context supplied. Order counterbalanced; latency not a clean treatment comparison.'}
    out=Path(args.out).resolve()
    manifest_path=out/'manifest.json'
    config_hash=lab.digest(config)
    if manifest_path.exists():
        if not args.resume or lab.read_json(manifest_path)['config_hash']!=config_hash:
            raise ValueError('Existing output requires --resume and identical frozen configuration')
    else:
        if args.resume or (out.exists() and any(out.iterdir())):
            raise ValueError('Use an empty new output directory')
        lab.write_json(manifest_path,dict(config,config_hash=config_hash,
                       started_at=datetime.datetime.now(datetime.timezone.utc).isoformat()))
    records=out/'records';records.mkdir(exist_ok=True)
    store=lab.WikiStore(out/'wiki_cache',args.timeout,3,shared_cache=lab.ROOT/'cache/wiki',
                        interval=1.5,budget=90.,ttl=604800.,audit=out/'retrieval.jsonl')
    try:
        for item,pair in zip(ordered,plan):
            for arm in pair['arms']:
                record=records/(arm+'-'+hashlib.sha256(item['_id'].encode()).hexdigest()[:20]+'.json')
                if record.exists():continue
                options=argparse.Namespace(**vars(args));options.state_feedback=arm=='react_state'
                start=time.monotonic()
                result=lab.solve(client,'react',item['question'],lab.Environment(store,args.obs_chars),options)
                # Reference answers are added only after model and tools have finished.
                em,f1=lab.score(result['prediction'],item['answer']) if result['status']=='finished' else (0.,0.)
                row=dict(result,schema=lab.VERSION,mock=False,method=arm,id=item['_id'],
                         question=item['question'],gold=item['answer'],em=em,f1=f1,
                         seconds=round(time.monotonic()-start,3))
                lab.write_json(record,row)
                print('%s %s %s EM=%s calls=%s'%(arm,item['_id'],row['status'],em,row['llm_calls']),flush=True)
                if result['status']=='api_error':
                    raise RuntimeError('API failed; record retained; do not silently replace failed attempts')
    finally:
        lab.report(out)
        comparison(out)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',required=True);p.add_argument('--out',required=True)
    p.add_argument('--phase',required=True,choices=['development','evaluation'])
    p.add_argument('--exclude',nargs='*',default=[])
    p.add_argument('--seed',type=int,default=233)
    p.add_argument('--max-steps',type=lab.positive,default=7)
    p.add_argument('--max-tokens',type=lab.positive,default=1024)
    p.add_argument('--obs-chars',type=lab.positive,default=4000)
    p.add_argument('--timeout',type=lab.positive,default=60)
    p.add_argument('--retries',type=lab.nonnegative,default=2)
    p.add_argument('--resume',action='store_true');p.add_argument('--verbose',action='store_true')
    run(p.parse_args())


if __name__=='__main__':main()
