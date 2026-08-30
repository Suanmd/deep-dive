# Usage

> deep-dive 的所有调用方式：CLI / 编程 / 高级。

## 1. CLI（最常用）

### 最简调用

```bash
deep-dive --query "<query>" --depth normal
```

输出在 `./tmp/deep-dive/<topic-slug>__<run-id>/`。

### 10 个最常用组合

```bash
# 1. 快速反馈（30 秒-2 分钟，2-3 task）
deep-dive --query "Python asyncio" --depth quick

# 2. 默认深度（8-12 task，10-15 分钟）
deep-dive --query "<query>"

# 3. 全深度（14+ task，30+ 分钟）
deep-dive --query "AGI safety" --depth full

# 4. 只用 MMX（省 Tavily 配额）
deep-dive --query "memory" --search-engine mmx --no-tavily

# 5. 只用 Tavily（无 mmx CLI 时）
deep-dive --query "renaissance" --search-engine tavily

# 6. 时间过滤（最近一周的新闻）
deep-dive --query "AI breakthrough" --depth quick --freshness week

# 7. 强制中文（即使 query 是英文）
deep-dive --query "machine learning" --lang zh

# 8. 自动本地语言（推荐）
deep-dive --query "日本战国历史" --lang auto

# 9. 自定义输出目录
deep-dive --query "deep learning" --output "D:/research/ai"

# 10. 调试模式（落盘每步状态）
deep-dive --query "kubernetes" --debug
```

### 所有 CLI flags

```
--query / -q TEXT        搜索主题（必填）
--depth                  quick | normal | full（默认 normal）
--freshness              '' | day | week | month | year
--lang                   auto | zh | en
--search-engine          auto | mmx | tavily
--no-tavily              强制只走 MMX
--output / -o PATH       输出根目录（默认 ./tmp/deep-dive）
--run-id TEXT            自定义 run id（强制 ASCII slug）
--max-workers INT        并发任务数（默认 2）
--min-chars INT          低质页阈值（默认 500）
--no-report              跳过 report.md
--no-capy                跳过卡皮观点 section
--debug                  落盘 <topic_dir>/debug/
--version                打印版本
--print-config           打印解析后的配置（JSON）后退出
```

## 2. 通过 python -m 调用

```bash
python -m deep_dive --query "..."
```

跟 `deep-dive` 等价，但不需要先 `pip install`。

## 3. 编程式调用（Python）

```python
from deep_dive import Orchestrator, load_config

cfg = load_config(overrides={"depth": "normal", "max_workers": 4})
orch = Orchestrator(cfg)
result = orch.run(query="<query>", run_id="ch15")

print(f"找到 {result.aggregated.total_urls} 个独立 URL")
print(f"报告: {result.report_path}")

for tr in result.task_results:
    print(f"  - {tr.note}: {tr.status.value} ({tr.url_count} URLs)")
```

高级用法：自定义引擎：

```python
from deep_dive.crawler.engines import SearchEngine, SearchHit
from deep_dive import Orchestrator, load_config

class BraveEngine(SearchEngine):
    name = "brave"
    def _raw_search(self, query, topk):
        # 调 Brave API，搜出 URL 列表
        return [SearchHit(url=u, title=t) for u, t in do_brave_search(query, topk)]

orch = Orchestrator(
    load_config(),
    engines={"mmx": BraveEngine(), "tavily": TavilyEngine(...)},
)
result = orch.run(query="...")
```

## 4. 编程式：直接用各组件

不想用 Orchestrator？每个子组件都是独立的：

```python
# 只用 URL canonical
from deep_dive.filters import canonicalize_url
canonical = canonicalize_url("HTTPS://Example.COM/path/?utm_source=x")

# 只用 URL 过滤
from deep_dive.filters import smart_filter_urls
kept = smart_filter_urls(urls, keep_per_domain=5)

# 只用 query 相关性
from deep_dive.relevance import is_query_irrelevant
if is_query_irrelevant(text, query):
    skip()

# 只用 query 变体
from deep_dive.query_variants import generate_variants
variants = generate_variants("<query>")
# {"original": "<query>", "en_query": "<book-title-en>", ...}
```

## 5. 编程式：自定义 fetcher

```python
from deep_dive.crawler.fetchers import Fetcher
from deep_dive import Orchestrator, load_config

class MyFastFetcher(Fetcher):
    name = "my-fast"
    def fetch(self, url, *, cookies=None, warmup_url=None):
        # 用 httpx 跳过浏览器渲染（快 10x，但拿不到 JS 内容）
        import httpx
        resp = httpx.get(url, timeout=10, cookies=cookies or {})
        return resp.text, ""

orch = Orchestrator(
    load_config(),
    fetchers={"primary": MyFastFetcher, "fallback": MyFastFetcher},
)
result = orch.run(query="...")
```

## 6. 在 Agent loop 中调用

deep-dive 设计上是 agent-friendly 的：

```python
# 在 OpenClaw / LangChain / 自研 agent 中
async def research_step(query: str, agent_state):
    cfg = load_config(overrides={"depth": "normal"})
    orch = Orchestrator(cfg)
    result = orch.run(query=query)
    # 把 report.md 喂给 LLM 做下游处理
    report = result.report_path.read_text(encoding="utf-8")
    return f"研究完成，找到 {result.aggregated.total_urls} 个来源：\n\n{report[:8000]}"
```

## 7. 高级：自定义报告模板

```python
from deep_dive.reporting.builder import build_report
from deep_dive.aggregator import Aggregator

# 跑搜索+抓取（用 Orchestrator）
result = orch.run(query=query)

# 改你自己的报告样式
custom_report = build_report(
    query=query,
    query_kind="tech",
    depth="normal",
    lang="auto",
    matrix=[],  # 你自己保存的 matrix
    task_results=result.task_results,
    aggregated=result.aggregated,
    output_dir=Path("./my-report"),
    min_chars=500,
    global_status="success",
)
```

## 8. 排错用法

```bash
# 看实际配置（不跑搜索）
deep-dive --query "test" --print-config

# 跑 quick + debug 模式（落盘所有步骤）
deep-dive --query "test" --depth quick --debug
# 看 tmp/deep-dive/<topic>__<run-id>/debug/heartbeat.log

# 强制不写报告（只看 summary.json）
deep-dive --query "test" --no-report
cat tmp/deep-dive/<topic>__<run-id>/summary.json
```

## 9. 批处理多个 query

```bash
# POSIX
for q in "<query>" "AI Agent" "文艺复兴"; do
  deep-dive --query "$q" --depth quick --output ./batch
done

# PowerShell
"<query>", "AI Agent", "文艺复兴" | ForEach-Object {
  deep-dive --query $_ --depth quick --output ./batch
}
```

或者用 shell 脚本（见 `examples/`）。

## 10. 退出码

deep-dive **总是 exit 0**（即使内部失败）。原因：PowerShell 在管道关闭时会有伪 exit code 1，混淆 agent loop。判断成功要看：
- `[DONE] all complete!` 这行出现
- `<output>/<topic>__<run-id>/report.md` 文件存在
- summary.json 里 `aggregated_summary.global_status` 是 `success` 或 `mixed`

如果 `summary.json.global_status` 是 `quota_exceeded` 或 `no_results`，说明需要等或换 query。
