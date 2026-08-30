# Agent 9 步工作流

> Agent（OpenClaw / LangChain / 自研 loop）集成 deep-dive 的标准工作流。

## 9 步流程总览

```
用户输入
  ↓
Step 0: 参数解析
  ↓
Step 1: 查询类型识别
  ↓
Step 2: 多语言扩展判断
  ↓
Step 3: 生成搜索矩阵
  ↓
Step 4: 调用 deep_search.py（并行 4-5 组）
  ↓
Step 5: 内部流程（Playwright + cloudscraper + cookie）
  ↓
Step 6: 提取 + 选材（关键）
  ↓
Step 7: 综合分析（agent 自身完成）
  ↓
Step 8: 写章节 tex（与 pdf_maker 协作）
  ↓
Step 9: 输出最终回答
```

## Step 0: 参数解析

用户输入 → 解析成 CLI 参数：

```python
args = parse_user_input("<query> 重点看 1587 年发生了什么")
# → {"query": "<query>", "depth": "normal", "focus": "1587 年"}
```

Agent 可以基于上下文智能推断 depth（"快速看看" → quick；"全面研究" → full）。

## Step 1: 查询类型识别

```python
from deep_dive.query_classifier import detect_query_kind
kind = detect_query_kind("<query>")  # → QueryKind.HUMANITIES
```

决定搜索关键词、排除站点、目标站点。

## Step 2: 多语言扩展判断

```python
from deep_dive.local_langs import detect_local_langs
langs = detect_local_langs("南美历史")  # → [LocalLang("es", "西文")]
```

中文 + 英文是基础；本地语言（如日文 / 法文 / 西文）是加餐。

## Step 3: 生成搜索矩阵

```python
from deep_dive.query_variants import generate_variants
variants = generate_variants("<query>")
# → {"original": "<query>", "en_query": "1587...", "critique": "...", ...}
```

```python
from deep_dive.orchestrator import build_search_matrix
matrix = build_search_matrix(
    query="<query>",
    config=cfg,
    variants=variants,
)
```

输出 `list[MatrixRow]`，每个 row = 一个并行任务。

## Step 4: 并行调用

```bash
# 一次性调用（推荐）
deep-dive --query "<query>" --depth normal --output ./tmp/wanli

# 或拆分并行（极少见，除非要自定义 task 集合）
deep-dive --query "<query> 综述" --depth quick --run-id g1 --output ./tmp/wanli &
deep-dive --query "<query> 学术" --depth quick --run-id g2 --output ./tmp/wanli &
deep-dive --query "<book-title-en>" --depth quick --run-id g3 --output ./tmp/wanli &
wait
```

## Step 5: 内部流程（不需 agent 介入）

deep-dive 自动处理：
- Playwright 抓取 → cloudscraper fallback
- Cookie 注入
- 正文抽取（trafilatura）
- 两阶段相关性检查

## Step 6: 提取 + 选材（关键）

deep-dive 输出 `report.md`，但 agent 通常需要按小节重组。

### read_reports.py 模板

```python
import re
from pathlib import Path

BASE = Path("./tmp/wanli")  # 输出根目录

g_info = [
    ('g1', 'search__wanli-g1', 'g1-content.txt'),
    ('g2', 'search__wanli-g2', 'g2-content.txt'),
    ('g3', 'search__wanli-g3', 'g3-content.txt'),
]

for gid, dir_name, out_name in g_info:
    report_path = BASE / dir_name / 'report.md'
    if not report_path.exists():
        print(f'{gid}: NOT FOUND')
        continue
    content = report_path.read_text(encoding='utf-8')

    # 提取 § 3. 全文内容 ... § 4. 元数据 之间的内容
    match = re.search(r'## 3\. 全文内容\s*\n(.*?)(?=\n## 4\. 元数据|\Z)',
                      content, re.DOTALL)
    body = match.group(1).strip() if match else content[content.find('## 全文内容'):]

    out_path = BASE / out_name
    out_path.write_text(body, encoding='utf-8')
    print(f'{gid}: extracted {len(body)} chars')

    # 同时输出 url 列表
    urls = re.findall(r'https?://[^\s\)）\]\"\'<>]+', body)
    unique_urls = list(dict.fromkeys(urls))
    (BASE / f'{gid}_urls.txt').write_text('\n'.join(unique_urls), encoding='utf-8')
    print(f'  unique URLs: {len(unique_urls)}')
```

### map_g_to_section.py 模板

按关键词匹配，把 g 组分配到章节小节：

```python
import re
from pathlib import Path

CH = 14  # 当前章节号
G_DIRS = [
    (1, 'search__wanli-g1'),
    (2, 'search__wanli-g2'),
    (3, 'search__wanli-g3'),
    (4, 'search__wanli-g4'),
]

SECTION_MAP = {
    "X.1": ["万历", "明朝", "张居正"],   # 第 1 节关键词
    "X.2": ["1587", "year", "significance"],  # 第 2 节
    "X.3": ["改革", "一条鞭法"],         # 第 3 节
}

for gid, dir_name in G_DIRS:
    fp = Path('./tmp/wanli') / dir_name / 'report.md'
    if not fp.exists():
        continue
    body = fp.read_text(encoding='utf-8')

    # 提取全文内容
    match = re.search(r'## 3\. 全文内容\s*\n(.*?)(?=\n## 4\. 元数据|\Z)',
                      body, re.DOTALL)
    body_text = match.group(1).strip() if match else ""

    for sec, kws in SECTION_MAP.items():
        hits = sum(body_text.count(k) for k in kws)
        if hits > 5:
            print(f"  g{gid} -> {sec}: {hits} keyword hits")
```

## Step 7: 综合分析（agent 自身）

读 `g1-content.txt` / `g2-content.txt` / `g3-content.txt` 后，agent 自己：
- 做主题归纳
- 检测知识缺口（必要时回 Step 4 加 depth）
- 评估证据强度
- 形成结构化笔记

LLM 在这一步消耗最大 token。**减少 token 用量**：
- 只喂 §3 全文内容（不含 §1 §2 元数据）
- 用 `min-chars` 过滤短文（默认 500）
- 对长报告分页读（每 8000 字符一批）

## Step 8: 写章节 tex

把选出来的素材 + URL 喂给 LaTeX 写章节流程：

```bash
# 调用 pdf_maker skill
pdf_maker --chapter ch14 \
  --material g1-content.txt g2-content.txt g3-content.txt \
  --url-pool g1_urls.txt g2_urls.txt g3_urls.txt \
  --output chapters/ch14.tex
```

pdf_maker 内部：章节标题 + 导读块 + 三线表 + URL 引用 + 小结 + 编译。

## Step 9: 输出最终回答

跟用户交互层：
- 报告 `report.md` 的核心结论
- 列出关键引用（带 URL）
- 指出局限性（如某些 task 失败）

## 主题分类与查询模板选择

| 主题类别 | 关键词特征 | 推荐 depth | run-id 命名建议 |
|---------|----------|----------|----------------|
| 政策法规 | 政策 / 指导意见 / 通知 / 法规 / 意见 | quick | `政策-<主题>` |
| 技术综述 | 综述 / 架构 / 机制 / 原理 | normal | `技术-<主题>` |
| 企业案例 | 公司名 / 试点 / 项目 / 案例 | normal | `案例-<公司>` |
| 白皮书/报告 | 白皮书 / 蓝皮书 / 报告 / 指引 / 规程 | normal | `报告-<发布机构>` |
| 市场规模/排行 | 排行榜 / 规模 / 占比 / 趋势 | normal | `市场-<主题>` |
| 对比/差异 | 差异 / 对比 / 对标 / 区别 | normal | `对比-<主题>` |
| 整体布局 | 整体 / 全域 / 区域 / 集群 | normal | `布局-<主题>` |
| 新兴/前沿 | 创新 / 前沿 / 趋势 / 展望 | full | `前沿-<主题>` |
| 学术研究 | 论文 / 研究 / 算法 / 模型 | full | `研究-<主题>` |
| 实战落地 | 试点 / 项目 / 应用 / 案例 | normal | `落地-<主题>` |

## Agent 集成示例（OpenClaw）

```python
from deep_dive import Orchestrator, load_config
from pathlib import Path

async def deep_research_step(query: str, agent_state):
    """OpenClaw agent skill：做一次深度研究。"""
    cfg = load_config(overrides={"depth": "normal"})
    orch = Orchestrator(cfg)
    result = orch.run(query=query)

    # 把 report.md 喂给下游 LLM
    report = result.report_path.read_text(encoding="utf-8")

    return {
        "summary": f"研究完成：{result.aggregated.total_urls} 个来源",
        "report_path": str(result.report_path),
        "report_excerpt": report[:4000],  # 前 4K 字符给 LLM 看
        "task_results": [
            {"note": tr.note, "status": tr.status.value, "url_count": tr.url_count}
            for tr in result.task_results
        ],
    }
```

## 性能 tip

- **快速探索**：`--depth quick`（~30 秒，2-4 task）
- **深度研究**：`--depth normal`（~10 分钟，7-8 task）
- **章节写作**：`--depth full`（~30 分钟，14 task）

不要默认用 full。full 用于：
- 章节素材采集（需要 100+ 来源）
- 长篇报告（章节数 ≥ 5）

quick 用于：
- 探索阶段（用户还没决定要写多深）
- 时间敏感任务（"5 分钟内给我一个 quick look"）

## 故障恢复

`report.md` 已经包含 auto_rescue 的拼救文件路径。Agent 检测到 `global_status != success` 时：
- `quota_exceeded` → 等 4h 或切引擎
- `no_results` → 改 query 重试
- `mixed` → 大部分成功，可以用，但需要人工审核
