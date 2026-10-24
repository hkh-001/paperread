# 论文阅读记录

围绕 Human-AI Symbiosis 与 LLM Agent，保存论文原文、阅读笔记与实践过程。

## 当前进度

截至 2026-10-24，已阅读 **2 / 8 篇**：第一篇 Agent 综述与 ReAct。ReAct 已完成五十题小规模三组对照，其余 6 篇为待阅读材料。

详细解读文件已经准备好，不代表已经读完。阅读状态以实际进度为准；具体阅读日期尚未确认，笔记中的 `date_read` 暂留空。

## 阅读索引

| 论文 | 阅读状态 | 实践进度 |
| --- | --- | --- |
| [A Survey on Large Language Model based Autonomous Agents](A%20Survey%20on%20Large%20Language%20Model%20based%20Autonomous%20Agents/A%20Survey%20on%20Large%20Language%20Model%20based%20Autonomous%20Agents%20-%20%E8%AF%A6%E7%BB%86%E8%A7%A3%E8%AF%BB.md) | 已读 | — |
| [ReAct - Synergizing Reasoning and Acting in Language Models](ReAct%20-%20Synergizing%20Reasoning%20and%20Acting%20in%20Language%20Models/ReAct%20-%20Synergizing%20Reasoning%20and%20Acting%20in%20Language%20Models%20-%20%E8%AF%A6%E7%BB%86%E8%A7%A3%E8%AF%BB.md) | 已读 | 已完成五十题三组对照 |
| [Generative Agents - Interactive Simulacra of Human Behavior](Generative%20Agents%20-%20Interactive%20Simulacra%20of%20Human%20Behavior/Generative%20Agents%20-%20Interactive%20Simulacra%20of%20Human%20Behavior%20-%20%E8%AF%A6%E7%BB%86%E8%A7%A3%E8%AF%BB.md) | 待阅读 | — |
| [LongMemEval - Benchmarking Chat Assistants on Long-Term Interactive Memory](LongMemEval%20-%20Benchmarking%20Chat%20Assistants%20on%20Long-Term%20Interactive%20Memory/LongMemEval%20-%20Benchmarking%20Chat%20Assistants%20on%20Long-Term%20Interactive%20Memory%20-%20%E8%AF%A6%E7%BB%86%E8%A7%A3%E8%AF%BB.md) | 待阅读 | — |
| [MemoryBank - Enhancing Large Language Models with Long-Term Memory](MemoryBank%20-%20Enhancing%20Large%20Language%20Models%20with%20Long-Term%20Memory/MemoryBank%20-%20Enhancing%20Large%20Language%20Models%20with%20Long-Term%20Memory%20-%20%E8%AF%A6%E7%BB%86%E8%A7%A3%E8%AF%BB.md) | 待阅读 | — |
| [Reflexion - Language Agents with Verbal Reinforcement Learning](Reflexion%20-%20Language%20Agents%20with%20Verbal%20Reinforcement%20Learning/Reflexion%20-%20Language%20Agents%20with%20Verbal%20Reinforcement%20Learning%20-%20%E8%AF%A6%E7%BB%86%E8%A7%A3%E8%AF%BB.md) | 待阅读 | — |
| [SOTOPIA - Interactive Evaluation for Social Intelligence in Language Agents](SOTOPIA%20-%20Interactive%20Evaluation%20for%20Social%20Intelligence%20in%20Language%20Agents/SOTOPIA%20-%20Interactive%20Evaluation%20for%20Social%20Intelligence%20in%20Language%20Agents%20-%20%E8%AF%A6%E7%BB%86%E8%A7%A3%E8%AF%BB.md) | 待阅读 | — |
| [Towards Emotional Support Dialog Systems](Towards%20Emotional%20Support%20Dialog%20Systems/Towards%20Emotional%20Support%20Dialog%20Systems%20-%20%E8%AF%A6%E7%BB%86%E8%A7%A3%E8%AF%BB.md) | 待阅读 | — |

## 如何记录阅读过程

1. 阅读后更新笔记的 `status` 与实际 `date_read`，补充自己的理解和疑问。
2. 实践过程中记录配置、实验结果、失败案例与下一步，完成后更新复现状态。
3. 每次实质进展后提交并同步：

```bash
git status
git add .
git commit -m "阅读进展：论文名与本次收获"
git push
```

首次提交仅保存已有材料；后续 Git 提交记录真实的阅读与实践进展。修改需 commit 和 push 后才会同步到 GitHub。
