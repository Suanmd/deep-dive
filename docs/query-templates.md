# Query Templates

> deep-dive 内置 5 类查询模板，决定搜索关键词、排除站点、目标站点。

## 5 类模板概览

| ID | 名字 | 触发关键词示例 | 排除站点 | 目标站点 |
|----|------|----------------|----------|----------|
| `humanities` | 人文社科 | 历史 / 文化 / 哲学 / 战争 / 传记 / 革命 | 低质下载站 | zhihu / weixin / wikipedia |
| `tech` | 技术编程 | 编程 / 代码 / API / 算法 / docker / tutorial | csdn / baike.baidu / sohu + 低质站 | stackoverflow / github / dev.to |
| `academic` | 学术研究 | 论文 / 研究 / 实验 / arxiv / review / survey / journal | 低质下载站 | arxiv / .edu / researchgate |
| `news` | 新闻时事 | 最新 / 今天 / news / breaking / announcement | 低质下载站 | reuters / bbc / apnews / reddit |
| `business` | 商业市场 | 公司 / 产品 / 投资 / 估值 / market / IPO | csdn / baike.baidu / sohu + 低质站 | bloomberg / reuters / ft / 36kr / huxiu |
| `general` | 通用 | （fallback） | 低质下载站 | （无） |

模板识别靠关键词匹配。**优先级**：`humanities > academic > tech > business > news` —— 第一个匹配命中胜出，避免歧义。

## 检测逻辑

实际 `detect_kind` 走 3 步（先拼写 compact、查 tech 缩写 / AI 实验室品牌，然后关键词打分）。完整实现在 `src/deep_dive/types.py`，文档不重复伪代码，直接引用源码即可。

## 触发示例

```python
>>> detect_query_kind("<query>")
QueryKind.HUMANITIES  # "历史" 在 "十五年" 上下文（无），但通用 fallback

>>> detect_query_kind("中国古代历史")
QueryKind.HUMANITIES  # "历史"

>>> detect_query_kind("machine learning")
QueryKind.TECH  # "learning" 没有明确关键词，所以走通用？
# 实际上 "machine" 不在 TECH 关键词，learning 也不在；fallback 到 GENERAL
```

> ⚠️ 检测是**关键词匹配**而非 NLP 模型。边界 case 可能误分类。如果你发现 query 应该归到 X 但走到了 GENERAL，可以：
> 1. 改 query 加个明确关键词（如 "machine learning tutorial"）
> 2. 在自定义 Config 里覆盖 `site_targets` / `exclude`

## 3 档深度

| 深度 | task 数 | topk/task | URL 总数（理论） | 耗时 |
|------|---------|-----------|------------------|------|
| `quick` | 2-4 | 14 | ~50 | 2-5 分钟 |
| `normal` | 7-8 | 18 | ~140 | 10-15 分钟 |
| `full` | 14 | 22 | ~300 | 30-50 分钟 |

实际 URL 数受搜索引擎配额 + 黑名单过滤影响，通常是理论值的 50-80%。

## 5+ 种查询视角

每个 task 自动派生以下变体（中文 query）：

| 视角 | 中文后缀 | 适用场景 |
|------|----------|----------|
| `original` | （原 query） | 基础搜索 |
| `refined` | "核心 关键 详解" | 聚焦要点 |
| `critique` | "争议 批评 不同观点 局限" | 反对视角 |
| `academic` | "学术 论文 期刊 作者立场 研究方法" | 学术视角 |
| `primary` | "原始资料 档案 一手 来源 官方" | 信源视角 |
| `comparative` | "对比 同类 横向 替代 优劣" | 对比视角 |
| `en_query` | （英译） | 英文基础 |
| `en_variant` | （英译 + "book review cases"） | 英文案例 |
| `en_academic` | （英译 + "academic paper..."） | 英文学术 |

英文 query 自动派生英文学术变体。

## 矩阵构造（伪代码）

```python
matrix = []

# 1. 中文任务（仅中文 query）
if is_chinese(query):
    matrix.append(MatrixRow("中文原始", variants["original"]))
    if depth in ("normal", "full"):
        matrix.append(MatrixRow("中文细化", variants["refined"]))
    if depth == "normal":
        matrix.append(MatrixRow("中文评论", variants["critique"]))
    elif depth == "full":
        # full: + 学术 + 一手
        matrix.append(MatrixRow("中文评论", variants["critique"]))
        matrix.append(MatrixRow("中文学术", variants["academic"]))
        matrix.append(MatrixRow("中文一手", variants["primary"]))

# 2. 英文基础（必有）
matrix.append(MatrixRow("英文基础", variants["en_query"]))
if depth in ("normal", "full"):
    matrix.append(MatrixRow("英文案例", variants["en_variant"]))
    matrix.append(MatrixRow("英文学术", variants["en_academic"]))

# 3. 本地语言（按需：日本/法国/南美...）
for lang in detect_local_langs(query):
    matrix.append(MatrixRow(f"{lang.name}查询", f"{query} {lang.name}"))

# 4. 站点定向（normal: 2 站, full: 3 站）
site_targets = template["site_targets"]
for site in site_targets[:n_sites]:
    matrix.append(MatrixRow(f"站点定向:{site}", f"{query} site:{site}"))

# 5. full 深度额外：领域扩展 + 对比 + Reddit/Medium 替代源
if depth == "full":
    matrix.append(MatrixRow("领域扩展", variants["refined"], extra_exclude))
    matrix.append(MatrixRow("对比视角", variants["comparative"]))
    if kind in ("tech", "news"):
        matrix.append(MatrixRow("Reddit讨论", f"{query} site:reddit.com"))
    for alt in MEDIUM_ALTERNATIVES[:2]:
        matrix.append(MatrixRow(f"Medium替代:{alt}", f"{query} site:{alt}"))

# 6. 通用补充：受众视角 + 反方视角（normal/full）
if depth in ("normal", "full"):
    if is_chinese:
        matrix.append(MatrixRow("P2-中文受众视角", f"{query} 读者评论 豆瓣 知乎"))
    matrix.append(MatrixRow("P2-反方视角", f"{query} criticism problems issues"))

return matrix[:max_queries]
```

## 自定义模板

两种方式：

### A. 覆盖 Config（不改源码）

```yaml
# config/defaults.yaml
depth_config:
  full:
    topk: 30              # 默认 22，改大
    max_queries: 20       # 默认 14，改大
```

### B. 在 Python 中改 template_for 返回值

```python
from deep_dive.query_classifier import template_for, QueryKind

# 修改模板的 site_targets
t = template_for(QueryKind.HUMANITIES)
t["site_targets"] = ["douban.com", "bilibili.com"] + t["site_targets"]
```

## 实战 tips

1. **泛 query 加限定词**：避免 "AI" 这种 1 字 query；用 "AI Agent evaluation 2026"
2. **学术 query 加引用要求**：触发 academic 模板靠 "论文/研究/期刊"，但英文 query 用 "arxiv" / "doi" 更准
3. **新闻 + 时间**：用 `--freshness week` 让搜索引擎先过滤
4. **本地语言**：deep-dive 自动检测国家名（"南美" → es、"日本" → ja）；无需手动

## 已知不足

- **关键词匹配不是 NLP**：同义词（"文章"/"paper"/"essay"）不互通
- **优先级写死**：如果未来 query 同时有 "历史" 和 "技术"，永远归 humanities
- **不缓存模板结果**：每次 query 都重新分类（开销 < 1ms，可忽略）
