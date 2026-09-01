---
name: deep-dive
description: "面向 Agent 的深度研究引擎：多角度并行检索（MMX + Tavily）+ 多语种扩展 + 站点定向 + 全文抓取（Playwright + cloudscraper 兜底）+ Cookie 注入 + 去重聚合 + 结构化 Markdown 报告输出。CLI + Python API 双入口。"
---

# deep-dive

> 面向 Agent 的深度研究引擎：多角度并行检索 + 全文抓取 + Cookie 注入 +
> 去重聚合 + 结构化 Markdown 报告。

## 它做什么

给定一个研究 query，`deep-dive` 启动多个并行检索任务（不同视角、不同语种、
不同站点），用真实浏览器抓取 Top 结果，做去重 + 相关性过滤，最后写出一份
四段式 Markdown 报告 + Capy 自动摘要（主题聚类 + 关键引用 + 多空论据）。

**v1.0.0 关键能力**：
- **智能质量门控** — info-density 评分替代纯字数阈值，数据密集短文不再被误判
- **多义词 corpus 主题聚类** — OPD 这种 5 种语义混杂的 query，按 host 归属到 AI/法律/残障服务/心理学/医学 等独立桶
- **段落级 Capy 论点抽取** — 每条论点带来源归属 + 质量评分（length + data density + source authority）
- **kind 自动检测** — `training` / `on-policy` / `policy` / `agentic` 等关键词触发 tech kind，自动加 `arxiv.org / github.com / paperswithcode.com` 定向
- **音乐/媒体域名豁免** — `music.apple.com` / `kuwo.cn` / `网易云` 等平台短内容页合法保留
- **PDF / DOC 二进制 URL 跳过** — 防止 PDF 二进制解码成乱码后被 char count 推上 Top URL
- **Pipeline fetch 并发** — `asyncio.gather + Semaphore(3)` 取代串行，单 task 内 17 个 fetch 从 ~510s 降到 ~180s

## 何时使用

- 需要**结构化深度研究报告**（中文或英文均可）
- 需要对比**同一主题的多个角度**（技术 / 学术 / 反方 / 一手信源）
- 需要做**站点定向深挖**（比如 arxiv.org 关于 Transformer 变体的全部内容）
- 需要登录墙内容（已配置 `config/cookies.json`）
- 多义词 query 需要区分语义（如 OPD = AI / Maryland 法律 / PA 残障服务）

## 何时不要用

- 只需**单条快速查询** → 用普通 web search 即可
- 需要**实时数据** → deep-dive 自带引擎延迟，不可用于高频实时场景
- 需要**逐字精确引用** → 搜索引擎可能改写，下游抓取是 best-effort

## 用法

### CLI

```bash
deep-dive --query "<研究主题>" --depth normal
deep-dive --query "黄金 走势" --depth quick --no-capy
deep-dive --query "your topic" --depth full --output ./my-research
```

### Python 模块

```bash
python -m deep_dive --query "your topic" --depth normal
```

### 程序化模式（LLM 驱动，推荐）

宿主 LLM 生成 `ResearchPlan` JSON（描述 variants / target_sites / english_search_terms），
通过 `--plan` 传入或直接调用 API：

```python
from deep_dive import Orchestrator, Config
from deep_dive.types import ResearchPlan

plan = ResearchPlan(
    query="<研究主题>",
    kind="tech",
    depth="normal",
    language_priority="balanced",
    english_search_terms=[
        "Attention Is All You Need Vaswani 2017 paper",
        "Transformer variants survey BERT GPT",
    ],
    variants={
        "refined": "transformer 架构 原理 自注意力",
        "critique": "transformer 局限 O(n²) 长序列",
        "academic": "transformer 综述 演进",
        "primary": "Attention Is All You Need 原文",
        "comparative": "transformer vs RNN vs CNN",
    },
    target_sites=["arxiv.org", "paperswithcode.com"],
    relevance_threshold=0.30,
    rationale="Five lines: original paper + critique + variants + code + comparison",
)
orch = Orchestrator(Config())
result = orch.run(query="<研究主题>", plan=plan)
```

## 配置

**用户可调值都在配置文件里，不在代码里**：

| 文件 | 用途 |
|------|------|
| `config/defaults.yaml` | 引擎、超时、深度档、质量阈值、低质域名列表 |
| `config/cookies.example.json` | 模板；复制到 `config/cookies.json`（gitignored）给登录墙站点用 |

API key 环境变量：`TAVILY_API_KEY`、`TAVILY_API_KEY_BACKUP`。
也支持 `TAVILY_API_KEYS="k1,k2,k3"` 多 key 池（按序轮换，quota / auth / network / timeout 任一失败即切下一个）。

## 输出

每次 run 创建 `./tmp/deep-dive/<topic>__<run-id>/`：

- `report.md` — 主报告（4 段式 + Capy 摘要）
- `summary.json` — task-level 元数据（含引擎选择、fallback 链、attempt 次数）
- `raw/` — 每个 task 的原始 HTML/TXT + `metadata.json`
- `<topic>_raw_all.txt` — auto-rescue 拼救的全文（dedup=0 时才有）
- `debug/` — 每步状态（仅 `--debug` 时写）

## 模块布局

```
src/deep_dive/
├── __init__.py        # 公开 API 导出
├── __main__.py        # python -m deep_dive 入口
├── cli.py             # argparse CLI 入口
├── config.py          # Config dataclass + load_config()
├── constants.py       # 内部常量（TAG_* 标签等）
├── orchestrator.py    # 矩阵构建 + 并行调度
├── types.py           # 所有公开 dataclass + enum
├── local_langs.py     # 本地语种检测
├── query_classifier.py # kind 自动判定
├── query_variants.py  # plan → variants
├── relevance.py       # 两阶段相关性检查
├── aggregator.py      # 跨任务去重
├── rescue.py          # auto_rescue_raw
├── logging_setup.py   # UTF-8 safe_print（统一来源）
├── crawler/
│   ├── cookies.py     # Cookie 加载 + URL 匹配
│   ├── encoding.py    # 响应编码检测
│   ├── extraction.py  # trafilatura 封装
│   ├── pipeline.py    # 单 task 抓取 + 提取 + 相关性
│   ├── blacklist.py   # 低质 host 模式
│   ├── engines/       # MMXEngine / TavilyEngine / DuckDuckGoEngine
│   └── fetchers/      # PlaywrightFetcher / CloudScraperFetcher
├── filters/
│   ├── canonical.py   # URL canonical 化
│   └── url_filter.py  # smart_filter_urls
└── reporting/
    ├── builder.py     # 4 段式 Markdown 报告
    └── capy_summary.py # Capy 摘要 section
```

## OpenClaw 用户注意

- 把 deep-dive 放在任意路径，OpenClaw 会自动发现 `SKILL.md`。
- 默认输出在 `<workspace>/tmp/deep-dive/`，可在 `config/defaults.yaml` 的
  `output_dir` 或 `--output` CLI 参数覆盖。
- 多语种 query 用 `--lang auto`（默认）会自动加本地语种变体。

## 已知 Quirk 与 Caveat

这些是真实行为点，LLM 写 plan 时应考虑。**不是 bug，但都会让新用户踩坑**。

- **`plan.depth` 只是文档字段**。`ResearchPlan` JSON 里的 `depth` 字段仅用于审计 / 可复现。**实际的任务上限（`max_queries`、`topk`）由运行时 `Config` 控制**，由 `--depth` CLI 参数或 `config/defaults.yaml` 决定。如果 plan JSON 写了 `"depth": "full"` 但忘了传 `--depth full`，只会拿到 normal 档（cap=8）。**严肃研究务必显式 `--depth full`。**

- **MMX 内置 `mmx-default` profile**。即使 `config/defaults.yaml` 里 `mmx_invocations: []`，日志里仍会出现 `[MMX-OK] mmx-default ...` —— 这是硬编码兜底 profile。要用自定义 MMX invocation，请在 config 里列出或重复传 `--mmx-invocation JSON`。

- **`site:reuters.com` / `site:bloomberg.com` / `site:pubmed.ncbi.nlm.nih.gov` 经 Tavily 返回 `no_results`**。这是 Tavily 自身限制，不是 query 问题。这些站点改用 MMX 走兜底。

- **Matrix `n_site` 上限随深度变化但仍是写死的**。`quick=0`、`normal=3`、`full=6`。如果 plan 有 8 个 sites，full depth 仍会丢 2 个。要么压减 `target_sites`，要么用 `--dry-run` 提前看被裁掉的是哪些。

- **进程退出码在成功时也可能为 1**。Playwright 异步浏览器 + 每个 fetch 的 asyncio loop 在 GC 时偶尔会让进程以 1 退出，即使 `report.md` / `summary.json` 都已正确落盘。`cli.py` 已经用 `os._exit(0)` 兜底，优先信任 `[DONE]` / `[EXIT-OK]` 日志标记，不要看 shell `$?`。

- **Windows MAX_PATH 安全**。长中文 / 带音标的 query 会产生 UTF-8 slug，加上 `__timestamp` 和嵌套 `raw/task/file`，可能超过 260 字符 Win32 默认。编排器在 `topic_dir` 超过 180 字符时自动回退到 12 字符 MD5 slug。

- **进程退出码 BUG**：`cli.py` 显式 `os._exit(0)`，配合 `__main__` 入口的兜底，强制以 0 退出。

- **report.md 编码 BUG**：Windows 默认 GBK 解码时中文乱码，使用 `utf-8-sig`（带 BOM），Windows 工具自动识别。

## 参见

- `docs/usage.md` — CLI cookbook
- `docs/architecture.md` — 数据流图
- `docs/engines.md` — 引擎选型
- `docs/cookies.md` — Cookie 配置
- `examples/` — 可运行示例脚本 + plan JSON