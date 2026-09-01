# URL Candidates Strategy

> deep-dive 抓取的 URL 在引用到正文（论文 / 报告 / 文章）时常常失效。本文讲如何维护一个**自己的** URL 候选池，提高下游写作的引用稳定性。

## 为什么需要候选池

deep-dive 抓取的 URL 在引用到正文（论文 / 报告 / 文章）时常常失效：
- 30 天后被原作者删除
- 文章 ID 漂移（同源文章日期 ±1 天）
- 政府站点改版、URL 重写
- SSL 错误 / 连接超时

写作前**必须**准备一份验证过的 URL 候选池，第一优先从中替换。

> ⚠️ **deep-dive 不维护任何特定领域的候选池**。这是用户责任（写作时因领域而异）。

## 候选池组织（按权威性分类）

```
url-pool/
├── government/      # 政府部委、官方政策文件
├── thinktank/       # 智库、研究院、学术机构
├── industry/        # 行业协会、龙头企业白皮书
├── province/        # 各省政府门户
├── vendor/          # 主要厂商技术文档
└── news/            # 主流媒体（注意时效性）
```

每个分类下放 5-20 个常用 URL + 一句话说明。

### 每个 URL 的元数据格式

```json
{
  "url": "https://www.gov.cn/zhengce/content/2024-03/28/content_6943xxx.html",
  "title": "国务院关于...的指导意见",
  "publish_date": "2024-03-28",
  "category": "government/policy",
  "tags": ["国务院", "数字经济", "指导意见"],
  "last_verified": "2026-08-31",
  "reliability": "primary",
  "backup_urls": [
    "https://www.gov.cn/zhengce/2024-03/28/content_6943xxx.htm",
    "https://web.archive.org/web/2026/https://www.gov.cn/zhengce/..."
  ],
  "notes": "原文 ID 偶尔会变；如果失效先试 .html → .htm"
}
```

字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| `url` | ✓ | 主 URL |
| `title` | ✓ | 文章标题 |
| `publish_date` | ✓ | 发布日期（YYYY-MM-DD） |
| `category` | ✓ | 路径片段（与目录结构一致） |
| `tags` | ✓ | 检索关键词 |
| `last_verified` | ✗ | 上次验证日期（YYYY-MM-DD） |
| `reliability` | ✗ | `primary` / `secondary` / `archive` |
| `backup_urls` | ✗ | 主 URL 失效时的候选 |
| `notes` | ✗ | 失效模式 + 替换策略 |

## 验证 SOP

```python
# verify_pool.py - 批量验证候选池 URL
import urllib.request, ssl, time, json
from pathlib import Path
from datetime import datetime

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

POOL_DIR = Path("./url-pool")

def verify_url(url: str, retries: int = 1) -> tuple[int, str, str]:
    """验证一个 URL。返回 (status, final_url, message)。"""
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (verify-pool)"}
            )
            resp = urllib.request.urlopen(req, context=ctx, timeout=10)
            status = resp.status
            final_url = resp.url
            # 校验是否重定向到 404
            if resp.url != url and "404" in resp.url:
                return (status, final_url, "redirect to 404")
            return (status, final_url, "OK")
        except Exception as e:
            if i == retries:
                return (0, url, f"ERR: {type(e).__name__}: {str(e)[:50]}")
            time.sleep(0.5)
    return (0, url, "max retries exceeded")

# 批量验证
for json_file in POOL_DIR.rglob("*.json"):
    candidates = json.loads(json_file.read_text(encoding="utf-8"))
    for entry in candidates:
        status, final, msg = verify_url(entry["url"])
        entry["_last_check"] = {
            "date": datetime.now().isoformat(),
            "status": status,
            "final_url": final,
            "msg": msg,
        }
        status_emoji = "✓" if status == 200 else "✗"
        print(f"  {status_emoji} {status} {entry['url'][:75]} — {msg}")
    json_file.write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
```

**跑法**：

```bash
# 一次性验证整个池子
python verify_pool.py

# 或单个 URL
python -c "from verify_pool import verify_url; print(verify_url('https://www.gov.cn/...'))"
```

**建议频率**：每月 1 次（很多站点文章会过期）。

## 通用失效模式（按经验规律分类）

写章节 tex 引用时，URL 失效常见以下几类。**写报告前先扫一遍**：

| 失效模式 | 触发条件 | 修复策略 |
|---|---|---|
| **大段拦截** | 百度百科、知乎、CSDN 等 | 改用其他百科（搜狗、头条）、备份站点 |
| **SSL 错误** | 部分政府门户、商业站点 | 加 `ctx.verify_mode = ssl.CERT_NONE` 重试，或换源 |
| **日期漂移** | 文章 URL 含日期路径（如 `2024-03/28/`） | 改日期 ±1 天 / 用站内搜索找原文 |
| **30 天后失效** | 新浪财经、转载文章 | 用 Wayback Machine 归档，或换原始来源 |
| **文章 ID 漂移** | 求是网、党媒类 | 用站内搜索功能定位新 URL |
| **跳转 404 页面** | 搜狐等带跳转的站点 | 校验 `resp.url != url`，标记 BAD |
| **域名整体迁移** | 旧 `.com` 改 `.gov.cn` 等 | 站内搜索原标题定位新 URL |
| **中文字符 URL 编码错** | URL 含中文 path | `urllib.parse.quote(parts.path, safe="/")` |
| **CDN 限速** | 大型站点（csdn 等） | 加 retry + backoff |
| **Bot 检测** | Cloudflare / Incapsula 等 | 走 cookies.json 或换 IP |

## 实战 SOP（章节写作前必跑）

1. **维护候选池**：每完成一章节，把这章新发现的可靠 URL 沉淀到对应分类
2. **写作前验证**：用 `verify_pool.py` 跑一遍候选池，过滤失效 URL
3. **fallback 顺序**：原 URL → 候选池同分类 → 候选池其他分类 → 站内搜索
4. **3 轮迭代**：
   - 第 1 轮 OK 链接直接用
   - 第 2 轮失败的从候选池替换
   - 第 3 轮还失败的最后人工介入

## 候选池获取策略

### 政府/政策类

- **gov.cn**：通过 `site:gov.cn` Google 搜
- **统计局**：从国家统计局官网首页翻
- **部委**：每个部委官网的"政策法规"栏目
- **省/市**：各省市官网

### 智库/学术类

- **社科院**：直接进官网
- **985/211 学术机构**：机构知识库
- **CASS**：中国社会科学院
- **UN/CEEPR**：国际机构

### 行业/产业类

- **行业协会**：每个行业的"中国 XX 协会"
- **龙头企业**：上市公司公告（巨潮 / 上交所）
- **白皮书发布机构**：行业咨询公司（IDC、Gartner、艾瑞、易观）

### 媒体类

- **官方媒体**：人民日报、新华社、央视
- **市场化媒体**：财新、第一财经、虎嗅、36 氪
- **国际媒体**：Reuters、BBC、FT、NYT

## 写作时的引用选择 SOP

```python
def select_citation_for_claim(claim: str, url_pool: dict) -> str:
    """给定一个声明，从候选池选最权威的 URL。"""
    candidates = search_pool(url_pool, claim)
    if not candidates:
        return ""  # 没找到，让 LLM 自己搜

    # 优先级：government > thinktank > industry > news > vendor
    priority = {"government": 1, "thinktank": 2, "industry": 3, "news": 4, "vendor": 5}
    candidates.sort(key=lambda c: priority.get(c["category"].split("/")[0], 99))

    # 取第一个还活着的
    for c in candidates:
        status, _, _ = verify_url(c["url"])
        if status == 200:
            return c["url"]

    return ""  # 全部失效
```

## 与 deep-dive 的协作

deep-dive 抓到的 URL **直接是新鲜的**，可以作为临时候选池：

```python
# 把当次 deep-dive 结果写入候选池
result = orch.run(query="AI Agent 评测")
for url, meta in result.aggregated.url_meta.items():
    if meta.status.value == "success" and meta.chars > 1000:
        # 写到候选池
        pool_entry = {
            "url": url,
            "title": meta.title,
            "publish_date": infer_date(url),  # 启发式
            "category": "deep-dive-tmp",
            "tags": [result.topic],
            "last_verified": datetime.now().isoformat()[:10],
            "reliability": "secondary",
        }
        save_to_pool(pool_entry)
```

下次写作时优先用候选池的，deep-dive 临时结果做补位。

## 跨章节复用

```python
# 把多章节的可靠 URL 合并到主候选池
def merge_candidate_pools(chapter_pools: list[dict]) -> dict:
    """合并多章节的临时池，按可靠性去重。"""
    merged = {}
    for pool in chapter_pools:
        for entry in pool["candidates"]:
            url = entry["url"]
            if url not in merged:
                merged[url] = entry
            else:
                # 已有条目，保留可靠性更高的
                existing_reliability = merged[url].get("reliability", "secondary")
                new_reliability = entry.get("reliability", "secondary")
                reliability_rank = {"primary": 1, "secondary": 2, "archive": 3}
                if reliability_rank.get(new_reliability, 2) < reliability_rank.get(existing_reliability, 2):
                    merged[url] = entry
    return list(merged.values())
```

## 长期维护

- **每月**：跑 `verify_pool.py` 一遍
- **每章完成后**：沉淀这章新发现的 URL
- **每年**：整理归档失效 URL（移到 `archive/` 子目录）
- **项目结束时**：打包整个 `url-pool/` 给团队复用

## 自动化建议

把 `verify_pool.py` 加到 CI（GitHub Actions / GitLab CI）：

```yaml
# .github/workflows/verify-pool.yml
name: Verify URL Pool
on:
  schedule:
    - cron: '0 0 1 * *'  # 每月 1 号
  workflow_dispatch:

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python scripts/verify_pool.py
      - uses: actions/upload-artifact@v4
        with:
          name: pool-verification
          path: url-pool/_report/
```

定期运行，自动产出失效 URL 报告，PR 通知维护者更新池子。
