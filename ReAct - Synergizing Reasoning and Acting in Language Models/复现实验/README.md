# ReAct 复现实验

从 [中文教程](教程.md) 开始。Python 3.9+，无需第三方依赖。

```bash
python3 -m unittest discover -s tests -v
python3 react_lab.py run --mock --methods direct cot react act --out runs/01-smoke --verbose
```

第二条是模拟模型的软件自检，不代表真实模型成绩。配置 `.env` 后可运行真实模型、本地工具或 HotpotQA + 在线维基百科，详见教程第 5–8 节。

- [完整教程](教程.md)
- [实验报告模板](实验报告模板.md)
- [主程序](react_lab.py)
- [可编辑提示词](prompts.json)
- [软件验证记录](验证记录.md)
