"""Export completed raw runs and readable paired traces; no model calls."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import react_lab as lab
import compare_state_feedback as experiment


def fence(text):
    marker='`'*max(3,max([len(s) for s in re.findall(r'`+',text)]+[0])+1)
    return marker+'text\n'+text+'\n'+marker


def markdown(path, lines):
    # Readable exports normalize trailing spaces; raw records stay byte-for-byte intact.
    path.write_text('\n'.join(line.rstrip() for line in '\n'.join(lines).splitlines())+'\n',encoding='utf-8')


def export(source,destination,data):
    result=experiment.comparison(source)
    if not result['complete']:raise ValueError('Do not export a partial experiment as complete')
    destination.mkdir(parents=True,exist_ok=True)
    manifest=lab.read_json(source/'manifest.json')
    for name in ['manifest.json','summary.json','summary.md','results.csv','comparison.json',
                 'retrieval.jsonl','retrieval_summary.json']:
        shutil.copy2(source/name,destination/name)
    with (source/'results.csv').open(encoding='utf-8-sig',newline='') as f:
        csv_rows=list(csv.reader(f))
    with (destination/'results.csv').open('w',encoding='utf-8-sig',newline='') as f:
        csv.writer(f,lineterminator='\n').writerows(csv_rows)
    shutil.copytree(source/'records',destination/'records',dirs_exist_ok=True)
    items={q['_id']:q for q in lab.load_items(data)}
    lab.write_json(destination/'题目集.json',[items[i] for i in manifest['question_ids']])
    meta=Path(str(data)+'.meta.json')
    if meta.exists():shutil.copy2(meta,destination/'题目集元数据.json')
    shutil.copy2(ROOT/'data/hotpot_dev_react.json.source.json',destination/'数据来源.json')
    rows=[lab.read_json(p) for p in (source/'records').glob('*.json')]
    groups={a:{r['id']:r for r in rows if r['method']==a} for a in experiment.ARMS}
    traces=destination/'逐题配对轨迹';traces.mkdir(exist_ok=True)
    for number,i in enumerate(manifest['question_ids'],1):
        a,b=(groups[arm][i] for arm in experiment.ARMS)
        lines=['# '+i,'','问题：'+a['question'],'','参考答案：'+a['gold'],'',
               '| 版本 | 答案 | EM | 状态 | 模型调用 |','| --- | --- | --- | --- | --- |']
        for label,row in [('原版',a),('页面状态版',b)]:
            lines.append('| %s | %s | %s | %s | %s |'%(label,row['prediction'].replace('|','\\|'),row['em'],row['status'],row['llm_calls']))
        for label,row in [('原版',a),('页面状态版',b)]:
            lines+=['','## '+label,'','初始问题消息：','',fence(row['initial_messages'][1]['content'])]
            for e in row['trace']:
                lines+=['','### 第 %s 次模型调用'%e['step'],'',fence(e.get('raw_output',''))]
                for key,title in [('format_error','格式拒绝'),('error','调用错误'),('observation','环境返回'),
                                  ('tool_state_feedback','附加页面状态')]:
                    if key in e:lines+=['',title+'：','',fence(e[key])]
        markdown(traces/('%02d-%s.md'%(number,i)),lines)
    a,b=(result['groups'][arm] for arm in experiment.ARMS)
    audit=lab.read_json(source/'retrieval_summary.json')
    n=a['n'];delta=round((b['em']-a['em'])*n)
    lines=['# 页面状态提示配对实验报告','','日期：2026-10-24。阶段：'+manifest['phase']+'。',
           '',f'已完成同一批 {n} 题的两个版本，共 {2*n} 次真实作答。每题最多 7 次模型调用；未按结果补跑或改分。',
           '','## 结果','',
           '| 指标 | 原版 ReAct | 页面状态版 |','| --- | --- | --- |',
           '| EM | %s/%s（%.1f%%） | %s/%s（%.1f%%） |'%(round(a['em']*n),n,a['em']*100,round(b['em']*n),n,b['em']*100),
           '| 答案 F1 | %.3f | %.3f |'%(a['f1'],b['f1'])]
    for k,label in [('empty_page_lookups','空页面 Lookup 次数（主指标）'),('max_steps','步数耗尽题数'),
                    ('format_errors','格式错误次数'),('tool_errors','工具故障题数'),('llm_calls','模型调用总数'),
                    ('tool_calls','工具动作总数'),('prompt_tokens','输入 token'),('completion_tokens','输出 token')]:
        lines.append('| %s | %s | %s |'%(label,a[k],b[k]))
    lines+=['','同题配对：状态版独对 %s 题，原版独对 %s 题，净变化 %+d 题。精确 McNemar 双侧 p=%.4f。'%
            (result['state_only_correct'],result['baseline_only_correct'],delta,result['mcnemar_exact_two_sided_p']),
            '','## 如何理解','']
    if a['empty_page_lookups']==b['empty_page_lookups']==0:
        lines+=['本轮两版都没有出现目标故障，不能据此认定状态提示消除了空页面 Lookup。旧轮故障未在原版重现，也表明单轮轨迹会变化。']
    else:
        lines+=['主指标从 %s 次变为 %s 次。该指标只衡量空页面上的无效 Lookup，不涵盖所有搜索偏航或停止错误。'%
                (a['empty_page_lookups'],b['empty_page_lookups'])]
    if delta==0:lines+=['本批题的 EM 没有净提高；应结合逐题得失与开销，而不是只看模型调用次数。']
    elif delta>0:lines+=['本批题的 EM 有所提高，但这是一轮小样本比较，不能视为普遍收益或显著性已成立。']
    else:lines+=['本批题的 EM 下降，不能把页面状态提示作为已验证有效的准确率改进。']
    lines+=['额外提示会增加上下文长度；模型调用较少也不必然意味着 token 更少。未估算金额。',
            '','## 实验边界','',
            '- 只新增页面状态反馈；保持模型、提示词、工具语义、7 步限制与评分规则。该开关默认关闭。',
            '- 题目内两版交替先后运行，共享同一轮百科响应快照；缓存与执行顺序使耗时不能作为纯方法速度比较。',
            '- 新五十题与旧 dev10、旧 eval50 均不重叠；开发阶段十题是已知题，不能称独立验证。',
            '- 模型请求 ID 为 deepseek-flash，无法锁定服务端权重快照；temperature=0 不保证完全可重复。',
            '- 失败与未提交按零分计入分母。参考答案只在交互后用于评分，未提供原始 supporting facts。',
            '', '检索审计：HTTP 尝试 %s 次，429 %s 次，额外重试 %s 次，缓存命中 %s 次。'%
            (audit['network_attempts'],audit['http_429'],audit['retry_attempts'],audit['cache_hits']),
            '','## 查看记录','',
            '- [逐题结果](results.csv)、[统计 JSON](comparison.json)、[冻结配置](manifest.json)。',
            '- 每题两个版本的模型输出、格式拒绝、真实观察和状态反馈见 `逐题配对轨迹/`；原始记录见 `records/`。',
            '- 题目数据来源、哈希和许可见 [数据来源](数据来源.json) 与 [题目集元数据](题目集元数据.json)。',
            '- 来源为 ReAct 作者发布的 HotpotQA 简化开发集；HotpotQA 数据采用 CC BY-SA 4.0。','']
    markdown(destination/'对照报告.md',lines)
    print('Exported',destination,'records',len(rows))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--dest',required=True);p.add_argument('--data',required=True)
    a=p.parse_args();export(Path(a.run),Path(a.dest),Path(a.data))
