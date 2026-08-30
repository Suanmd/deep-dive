# Output Format

> deep-dive 在 `<output_dir>/<topic-slug>__<run-id>/` 下生成的文件全解。

## 目录布局

```
tmp/deep-dive/
└── <topic-slug>__<run-id>/                # 例如：search__20260823_101831
    ├── report.md                          # 主报告（4 段式 + 卡皮观点）
    ├── summary.json                       # 任务元数据（机器可读）
    ├── <topic>_raw_all.txt                # auto_rescue 拼救的全文（可选）
    ├── raw/                               # 原始 task 输出
    │   ├── task_00/
    │   │   ├── metadata.json              # per-task metadata
    │   │   ├── url_mapping.json           # txt_file → URL 映射
    │   │   └── *.html / *.txt             # 抓取的页面
    │   ├── task_01/
    │   │   └── ...
    │   └── ...
    └── debug/                             # --debug 模式下才有
        ├── heartbeat.log                  # 调度心跳
        └── matrix.json                    # 完整搜索矩阵
```

## report.md 结构

### 头部元数据

```markdown
# Deep Dive Report: <query>

**生成时间**：&lt;YYYY-MM-DD HH:MM:SS&gt;（实跱时戳）
**查询类型**：humanities / tech / academic / news / business / general
**搜索深度**：quick / normal / full
**语言**：zh / en / auto
**任务总数**：14（成功 13）
**URL 总数**：178
**结果**：成功 142 / 拦截 12 / 失败 8 / 低质（<500字）16 / 黑名单域名 0
```

### 警告块（条件出现）

只在 `global_status != success` 时显示：

```markdown
> ⚠️ **[QUOTA 警告]** 本次搜索多个 task 触发 MMX 配额耗尽 (`exceeds your plan`)。
> 建议：1) 等 4h 后重试；2) 减 `--depth=quick`；3) 考虑 `--no-tavily` 避免双重消耗。
```

类型有 `[QUOTA 警告]` / `[EMPTY 警告]` / `[MIXED 警告]` 三种。

### §1 任务执行情况

表格列每个 task 的执行状态：

```markdown
## 1. 任务执行情况

| # | 任务 | 状态 |
|---|------|------|
| 1 | 中文原始 | OK |
| 2 | 英文基础 | OK |
| 3 | 中文细化 | quota_exceeded |
```

状态值：`OK` / `quota_exceeded` / `no_results` / `failed` / `timeout` / `irrelevant`。

### §2 URL 来源汇总

去重后的 URL 列表，按"被多少 task 命中"倒序：

```markdown
## 2. URL 来源汇总

**共 178 个独立 URL**

| # | 标题 | URL | 字数 | 来源 |
|---|------|-----|------|------|
| 1 | <query> 1587 年... | https://example.com/... | 15420 | 中文原始, 英文基础 |
| 2 | 1587: A Year of No... | https://wikipedia.org/... | 8200 | 英文基础 |
```

最多显示 60 行。超出的在 `summary.json` 里看完整列表。

### §3 全文内容

每个成功抓取的 URL 的全文（按字数倒序）：

```markdown
## 3. 全文内容

### 1. <query> 1587 年...

**URL**: https://example.com/article-1587

**来源**: 中文原始

```
<文章全文，最多 12000 字符>
```

---

### 2. 1587: A <book-title-en>

...
```

跳过：
- 字符数 < `--min-chars`（默认 500）的低质页
- 黑名单域名（`LOWQ_DOMAINS` / `SPAM_DOMAINS`）

这些被跳过但确实成功的页挪到 §4。

### §4 低质页

```markdown
## 4. ⚠️ 低质页（16 个，已从正文剔除）

- 320 bytes | [low_chars] | 文章标题 | https://...
- 850 bytes | [blacklisted_domain] | 文章标题 | https://...
```

原因：`low_chars` 或 `blacklisted_domain`。

### §5 元数据 JSON

```markdown
## 4. 元数据

```json
{
  "query": "<query>",
  "type": "humanities",
  "depth": "normal",
  "tasks": 14,
  "success_tasks": 13,
  "unique_urls": 178,
  "time": "&lt;YYYY-MM-DD HH:MM:SS&gt;",
  "global_status": "success"
}
```
```

### 卡皮观点 section（末尾追加）

deep-dive 还会在 report.md 末尾追加「## 🎀 卡皮观点」section，由 `reporting.capy_summary.append_capy_section` 生成。

详见下文。

## summary.json 结构

机器可读版完整状态：

```json
{
  "query": "<query>",
  "depth": "normal",
  "lang": "auto",
  "matrix_count": 14,
  "task_results": [
    {
      "note": "中文原始",
      "query": "<query>",
      "status": "success",
      "url_count": 18,
      "duration_seconds": 12.4,
      "output_dir": "tmp/deep-dive/search__20260823_101831/raw/task_00",
      "error": null
    },
    ...
  ],
  "aggregated_summary": {
    "total_urls": 178,
    "global_status": "success"
  },
  "timestamp": "&lt;ISO-8601 timestamp&gt;",
  "config": {
    "version": "1.0.0",
    "depth": "normal",
    "lang": "auto",
    ...
  }
}
```

每个 `task_results[i]` 对应一个 matrix row（按运行顺序）。

## 卡皮观点 section 详解

`reporting.capy_summary.append_capy_section` 在 `report.md` 末尾追加：

```markdown
---

## 🎀 卡皮观点（自动生成 · &lt;YYYY-MM-DD HH:MM:SS&gt;）

> 本节由 deep_dive.reporting.capy_summary 追加。
> 读 report.md 全文提取主题词、关键引用、多空论据。**不调用 LLM**。

### 📊 抓取元数据摘要

| 指标 | 数值 |
|------|------|
| 总 URL | 178 |
| 成功 | 142 (79%) |
| 拦截 | 12 |
| 失败 | 8 |
| 中文来源 | 95 |
| 英文来源 | 83 |
| 搜索任务数 | 14 |
| 报告文章数 | 142 |

### 🔍 搜索任务覆盖

  - 中文原始: success
  - 中文细化: success
  - 英文基础: success
  ...

### 🏆 抓取字数 Top 5 URL

  1. [中文] <query> 1587 年...
     https://example.com/article-1587
     字数: 15,420

### 📝 内容主题归纳

**高频关键短语**（Top 10）：
  - `dram` x 412
  - `长鑫存储` x 160
  - `长鑫科技` x 64
  ...

**多空博弈 + 事实数据**：

**预测/机构观点**：
- [预测句子 1]
- [预测句子 2]

**看涨论据**：
- [看涨句子 1]
...

**关键引用**：
  > [数字引用 1]
  > [数字引用 2]

### 💡 三个内容观点

**观点 1（内容主题）**：从 report.md 142 个文章 section 抽取高频词...
**观点 2（多空博弈）**：预测/机构观点 N 条 + 看涨论据 M 条 + 看跌论据 K 条...
**观点 3（数据质量）**：抓取 178 个独立 URL（成功 142），中文 95 / 英文 83...

### ⚡ 一句话总结

本次深度研究围绕「<query>」抓取 178 个独立 URL，覆盖 14 个搜索任务维度...
```

### 数据不足时的状态块

如果 `total_urls == 0` 或所有 task 都 failed，卡皮 section 不会写三个观点，而是写一个状态提示：

```markdown
---

## 🎀 卡皮观点（自动生成 · &lt;YYYY-MM-DD HH:MM:SS&gt;）

> **⚠️ [EMPTY] 数据不足** — 本轮抓取 0 个独立 URL，成功 0。
> 本轮未抓取到任何有效内容。建议：
> 1) 检查 query 是否太泛；
> 2) 检查 `raw/` 目录下 `metadata.json` 诊断；
> 3) 换 `--depth=full` 或重写查询词重试。
> **不生成三个观点，避免幻觉输出。**

```

**绝不**写"3 个明确观点"——零数据时强行总结会幻觉。

## raw/ 目录结构

每个 task 在 `raw/task_NN_<slug>/` 下：

```
raw/task_00_chinese-original/
├── metadata.json           # per-task metadata
├── url_mapping.json        # txt_file → URL
├── example_com_article_1.html    # 抓取的 HTML
├── example_com_article_1.txt     # 抽取的正文
└── ...
```

### metadata.json（per-task）

```json
[
  {
    "url": "https://example.com/article",
    "title": "文章标题",
    "status": "success",
    "chars": 15420,
    "html_file": "example_com_article.html",
    "txt_file": "example_com_article.txt",
    "source_task": "中文原始",
    "query_index": 0
  }
]
```

实时写入（每抓完一个 URL 就追加一次），所以即使中途崩溃也保留已抓的内容。

### url_mapping.json（per-task）

```json
{
  "example_com_article.txt": "https://example.com/article"
}
```

### 文件命名规则

URL → 文件名的转换：

```python
def safe_filename(url):
    parsed = urlparse(url)
    name = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", parsed.netloc + parsed.path)
    return name.strip("_")[:120]
```

`https://example.com/path/to/article` → `example_com_path_to_article`

最长 120 字符（防止某些 URL 路径过长）。

## auto_rescue 输出

`auto_rescue_raw` 在 dedup=0 或 raw 已有内容时，写 `<topic>_raw_all.txt`：

```
tmp/deep-dive/search__20260823_101831/
└── search__20260823_101831_raw_all.txt    # 自动拼救的全文
```

文件格式：

```
<段落 1，SHA1 去重>

============================================================

<段落 2>

============================================================

...
```

段落之间用 60 个 `=` 分隔。

## debug/ 目录（仅 --debug 模式）

```
tmp/deep-dive/search__20260823_101831/debug/
├── heartbeat.log           # 每 10s 一次的进度心跳
└── matrix.json             # 完整搜索矩阵（含所有 query 变体）
```

### heartbeat.log

```
1724287123.123|[HEARTBEAT] 3/14 done
1724287133.456|[HEARTBEAT] 7/14 done
...
```

每行格式：`<unix-timestamp>|[HEARTBEAT] <done>/<total> done`

### matrix.json

```json
{
  "query": "<query>",
  "depth": "normal",
  "variants": {
    "original": "<query>",
    "en_query": "<book-title-en>",
    "refined": "<query> 核心 关键 详解",
    "critique": "<query> 争议 批评...",
    ...
  },
  "matrix": [
    {
      "note": "中文原始",
      "query": "<query>",
      "topk": 18,
      "exclude": ["k73.com", "doc88.com", ...]
    },
    ...
  ]
}
```

用来 debug "为什么这个 task 没搜到"这类问题。
