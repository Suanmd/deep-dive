# Architecture

> deep-dive 的内部模块图、数据流、关键设计权衡。

## 总览（一句话）

```
用户 query → Orchestrator 调度 → N 个搜索+抓取 task 并行 → 聚合去重 → 报告
```

## 模块依赖图

```
                    ┌──────────────────────────────┐
                    │           CLI (cli.py)       │
                    │  argparse + UTF-8 safe print │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │       Orchestrator           │
                    │  搜索矩阵 + 并行 dispatch     │
                    │  + heartbeat + global watch  │
                    └──┬─────────────┬────────────┬┘
                       │             │            │
                       ▼             ▼            ▼
        ┌────────────────────┐ ┌──────────────┐ ┌─────────────────┐
        │  Query classifier  │ │  Variants    │ │  Local langs    │
        │  (5 types + prio)  │ │  (5+视角)    │ │  (10 langs)     │
        └────────────────────┘ └──────────────┘ └─────────────────┘

              ┌─────────────────────────────────────────────┐
              │            Pipeline (per task)              │
              │                                             │
              │   Search  ──►  Filter  ──►  Fetch           │
              │  (MMX/Tav)    (smart)    (Playwright/CS)    │
              │                                             │
              │                 ──►  Extract  ──►  Relevance│
              │                  (trafilatura)  (2-stage)   │
              └─────────────────────────────────────────────┘

        ┌────────────────┐    ┌─────────────────┐   ┌──────────────────┐
        │  Aggregator    │    │  Auto-rescue    │   │  Reporting       │
        │  (URL dedup    │───►│  (raw_all.txt)  │──►│  builder + capy  │
        │   + 段落 SHA1) │    │                 │   │                  │
        └────────────────┘    └─────────────────┘   └──────────────────┘
```

## 数据流（一次完整 run）

```
           ┌───────────────────────────────┐
Input:     │  query + Config + cookies     │
           └────────────┬──────────────────┘
                        │
                        ▼
           ┌───────────────────────────────┐
Step 0:    │  generate_variants(query)     │  →  dict[perspective, query]
Step 1:    │  detect_query_kind(query)     │  →  QueryKind enum
Step 2:    │  detect_local_langs(query)    │  →  list[LocalLang]
Step 3:    │  build_search_matrix(...)     │  →  list[MatrixRow]
           └────────────┬──────────────────┘
                        │
                        ▼
           ┌───────────────────────────────┐
Step 4:    │  Parallel dispatch            │
           │   for row in matrix:          │
           │     submit(_run_one_task, ...)│  →  list[TaskResult]
           └────────────┬──────────────────┘
                        │
                        ▼
           ┌───────────────────────────────┐
Step 5:    │  aggregate(task_results, ...) │  →  AggregatedResult
           │  - walk raw/*/metadata.json   │
           │  - dedup URLs (canonical)     │
           │  - record source_task per URL │
           └────────────┬──────────────────┘
                        │
                        ▼
           ┌───────────────────────────────┐
Step 6:    │  auto_rescue_raw(...)         │  →  writes <topic>_raw_all.txt
           │   if dedup==0 OR first run    │
           └────────────┬──────────────────┘
                        │
                        ▼
           ┌───────────────────────────────┐
Step 7:    │  build_report(...)            │  →  report.md (4 sections)
           └────────────┬──────────────────┘
                        │
                        ▼
           ┌───────────────────────────────┐
Step 8:    │  append_capy_section(...)     │  →  adds ## 卡皮观点 to report.md
           └────────────┬──────────────────┘
                        │
                        ▼
           ┌────────────────────────────────────────────────────────────┐
Output:    │  CrawlResult(topic, task_results, aggregated, report_path) │
           └────────────────────────────────────────────────────────────┘
```

## 关键设计权衡

### 为什么用 src/ layout

- **避免 import 冲突**：项目根目录不能有 `deep_dive/` 包，否则会被自动发现并覆盖 `src/deep_dive/` 版本。
- **PEP 517 友好**：`pyproject.toml` 的 `package-dir = {"" = "src"}` 让 `pip install -e .` 干净工作。
- **测试无需安装**：`conftest.py` 把 `src/` 插到 sys.path 首位，pytest 不依赖安装步骤。

### 为什么 engines/fetchers 分离

- **engines** 负责 "返回 URL 列表"（MMX / Tavily / Brave / Serper / 自定义）
- **fetchers** 负责 "给定 URL 返回 HTML"（Playwright / cloudscraper / httpx / 自定义）
- 两者可以独立替换。eg：写 Brave engine + httpx fetcher 是新组合，零架构改动。

### 为什么 smart_filter_urls 在多个层调用

- **Engine 层**：返回 URL 后立即过一遍 `smart_filter_urls`
- **Aggregator 层**：跨 task 再去重时也基于 canonical
- **双层冗余但语义不同**：engine 层防止低质 URL 进入抓取；aggregator 层防止重复 task 写同一个 URL

### 为什么 relevance 是两阶段

```
Stage 1: 单字密度 (单字 keyword 在 text 中命中率) ≥ 25%
Stage 2: 核心实体 (2+ 字中文/3+ 字英文) 命中率 ≥ 34%
```

历史 bug 教训：

| 阶段 | 解决什么问题 | 已知失败模式 |
|------|--------------|--------------|
| Stage 1 | 完全没 overlap 的文档 | 主题相关但用词不重叠 |
| Stage 2 | 高单字密度但错领域 | aliyun 菜单命中 "存/储/能/产" 但没 "长鑫" |

### 为什么 Orchestrator + MatrixRow 是 dataclass(frozen=True)

- **不可变**：避免多线程下 race condition（task 之间不会改到同一份 matrix row）
- **可哈希**：未来要做 task-level dedup 时方便（虽然目前按位置 dispatch）
- **轻量**：每行 ~150 bytes，跑 full 深度 14 task × 8 byte 引用 = 几 KB，无压力

### 为什么自动救援 (auto_rescue_raw) 即使有数据也跑

`auto_rescue_raw` 的生成条件是"`<topic>_raw_all.txt` 已存在且 > 1 KB 才跳过"。这一设计保证用户任何时候跑完都有 fallback 可读，原始数据不丢。

### 为什么 Logger 不全局劫持 print

早期版本曾尝试 `builtins.print = _safe_print`，导致所有第三方库的日志都被 emoji 替换过，调试反而困难。当前版本改为：
- 业务代码显式 `from deep_dive.logging_setup import safe_print`
- 第三方库行为不被污染
- 用户想静音可以 `Logger.disable()`

## 线程模型

```
Main Thread
  │
  ├─ Heartbeat thread (daemon) ──────── 每 10s print 进度
  │
  ├─ ThreadPoolExecutor(max_workers=N) ── 并发跑 task
  │     │
  │     ├─ Task Thread 0 ──► Engine (sub-threadpool) ──► Fetcher (sub-threadpool)
  │     ├─ Task Thread 1 ──► ...
  │     └─ Task Thread N ──► ...
  │
  └─ Auto-rescue + Report builder ─── single-thread
```

每个 task 内部又嵌套了一层 ThreadPoolExecutor：
1. Engine 的 `_raw_search` 跑在 sub-executor（带 timeout）
2. Fetcher 的 `_process_one` 跑在 sub-executor（带 retry）
3. 这两层都是 deep-dive 内部的并发控制，不暴露给用户

## 已知限制

- **Playwright 启动开销**：每个 PlaywrightFetcher 实例 ~300ms；单次 run 启动+关闭约 1s。对 quick depth 不划算。
- **trafilatura 中文支持**：trafilatura + 中文抽取 OK，但极个别版面（横排+竖排混排）会抽空。
- **Tavily 配额**：免费档 1000/月，超出按 task 失败处理（不抛 quota error 给用户，需要看 stderr tag 识别）。
