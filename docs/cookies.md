# Cookies

> 登录态站点（知乎、百度文库等）需要 Cookie 注入。本文讲 cookie 文件格式、导出指南、安全规范。

## 为什么需要 Cookie

部分站点对未登录用户返回降级内容：

| 站点 | 未登录 | 登录后 |
|------|--------|--------|
| zhihu.com | 仅前 3 个回答 | 完整内容 |
| baidu.com/wenku | "登录查看更多" | 完整文档 |
| weixin.qq.com | 文章列表 | 完整文章正文 |

deep-dive 抓取时通过 cookie 注入绕过。

## Cookie 文件位置

优先级（高 → 低）：

1. `DEEP_DIVE_COOKIE_FILE` 环境变量（绝对路径）
2. `<output_dir>/../config/cookies.json`（旧版布局兼容）
3. `./config/cookies.json`（项目根）

## 文件格式

`config/cookies.example.json`：

```json
{
  "zhihu": {
    "domain": ".zhihu.com",
    "cookies": [
      {
        "name": "z_c0",
        "value": "abc123|xyz789",
        "domain": ".zhihu.com",
        "path": "/"
      }
    ],
    "note": "（可选，仅自己看）",
    "last_updated": "2026-08-31"
  },
  "baidu_wenku": {
    "domain": ".baidu.com",
    "cookies": [
      {
        "name": "BDUSS",
        "value": "fake-bduss-value",
        "domain": ".baidu.com",
        "path": "/"
      }
    ]
  }
}
```

字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| 顶层 key（"zhihu"） | ✓ | 站点标识，deep-dive 内部用 |
| `domain` | ✓ | cookie 作用的域名（带前导点） |
| `cookies[]` | ✓ | 该域名的所有 cookie |
| `cookies[].name` | ✓ | cookie 名 |
| `cookies[].value` | ✓ | cookie 值 |
| `cookies[].domain` | ✓ | 跟哪个域匹配（通常跟外层一致） |
| `cookies[].path` | ✗ | 默认 "/" |

## 导出步骤（Chrome + EditThisCookie）

### 1. 安装扩展

[EditThisCookie](https://chrome.google.com/webstore/detail/editthiscookie/)（或 FngjC / Cookie-Editor 等同类）。

### 2. 登录

**重要**：用**专用小号**，不要用主账号。

理由：
- 爬虫请求有特征（高频 UA、缺失 referrer、并发），可能触发风控
- 主账号被封不可逆

### 3. 导出

1. F12 → Application → Storage → Cookies → 选中目标域名
2. 找到 `EditThisCookie` 图标 → "Export" → 选 JSON
3. 复制 JSON 数组（不是单个 cookie 对象）

### 4. 粘贴到 `config/cookies.json`

找出最关键的两个 cookie：

- 知乎：`z_c0`、`KLBRSID`、`d_c0`
- 百度：`BDUSS`、`STOKEN`、`PTOKEN`

格式化成 `cookies.json` 的 schema。

## URL 匹配逻辑

`deep_dive.crawler.cookies.match_cookies_to_url(url, cookies_map)`：

```python
url = "https://www.zhihu.com/question/123"
cookie_domain = ".zhihu.com"

# 匹配规则（任一满足即可）：
# 1. cookie.domain in url.host            ("zhihu.com" in "www.zhihu.com")
# 2. url.host.endswith(cookie.domain)     ("www.zhihu.com".endswith("zhihu.com"))
```

**当前行为**：返回该站点所有匹配 cookie（多 cookie 全部透传给浏览器）。

## 注入时机

deep-dive 在 Playwright 抓取前注入：

```python
# crawler/pipeline.py
async with browser.new_page(...) as page:
    if cookies:
        await page.context.add_cookies(cookies)
    await page.goto(url)
```

注入后立即访问 URL；后续 cookie 检查依赖服务端 session。

## 有效期

| 站点 | cookie 有效期 | 续期方式 |
|------|---------------|----------|
| zhihu.com | 1-3 个月 | 重新登录导出 |
| baidu.com | 1-2 个月 | 重新登录导出 |
| weixin.qq.com | 2-4 周 | 重新扫码 |

**检测过期**：

```powershell
# 跑一次已知需要登录的 query
deep-dive --query "知乎盐选" --depth quick

# 看 raw/<task>/ 下 html 文件
# 如果还是降级内容（"登录查看更多"），cookie 过期
grep -l "登录查看更多" tmp/deep-dive/<topic>__<run-id>/raw/*/page.html
```

## 安全规范

### 🚨 绝对不能做

1. **不要把 `config/cookies.json` commit 到 git** — 已经在 `.gitignore` 里
2. **不要把 cookie 发给别人或上传到任何地方**（Slack、邮件、Issue）
3. **不要用主账号** — 准备一个小号专门给爬虫用
4. **不要在公开 demo 仓库放真实 cookie** — 用 fake value（`"value": "fake"`）

### ✅ 应该做

1. 用 `.gitignore` 确保 `cookies.json` 不入版本控制
2. 仓库里只放 `cookies.example.json`（空数组或 fake value）
3. 定期检查 `last_updated` 字段，过期前主动续
4. 在 SECURITY.md 报告 cookie 泄漏事件

## 编程式加载

```python
from deep_dive.crawler.cookies import load_cookies, match_cookies_to_url

cookies_map = load_cookies()  # 从默认路径
# cookies_map = {"zhihu": [Cookie(...), ...]}

# 匹配特定 URL
url = "https://www.zhihu.com/question/123"
matched = match_cookies_to_url(url, cookies_map)
# matched = [{"name": "z_c0", "value": "...", "domain": ".zhihu.com", "path": "/"}]
```

## 多账号场景

同一个站点多个账号：

```json
{
  "zhihu_account_a": {
    "domain": ".zhihu.com",
    "cookies": [{"name": "z_c0", "value": "AAA", ...}]
  },
  "zhihu_account_b": {
    "domain": ".zhihu.com",
    "cookies": [{"name": "z_c0", "value": "BBB", ...}]
  }
}
```

deep-dive 当前版本不会自动轮换。**手动切换**：

```python
from deep_dive.crawler.cookies import load_cookies, match_cookies_to_url

cookies_map = load_cookies()
# 选一个账号
cookies_map = {"zhihu_account_a": cookies_map["zhihu_account_a"]}

# 然后传给 Orchestrator（通过自定义 fetcher）
```

## 常见问题

### cookie 没生效

1. 检查 `domain` 字段（必须带前导点）
2. 检查 cookie 是否过期
3. 看 stdout 的 `[COOKIE]` 日志：
   - `[COOKIE] no cookies.json found (optional)` → 文件路径不对
   - `[COOKIE] loaded N cookies across M sites` → 加载成功
   - `[COOKIE-WARN] inject failed: ...` → 注入失败（cookie 格式错）

### cookie 注入后还是被风控

- 降低 `--max-workers` 到 1
- 加 `--freshness` 减少请求频率
- 用专门的小号，不要和日常账号混

## 进阶：自定义 Cookie 注入器

```python
from deep_dive.crawler.fetchers import PlaywrightFetcher
from deep_dive.crawler.cookies import load_cookies, match_cookies_to_url

class MyFetcher(PlaywrightFetcher):
    async def afetch(self, url, *, cookies=None, warmup_url=None, do_warmup=False):
        # 自定义 cookie 来源（比如从数据库）
        cookies_map = load_my_db_cookies()
        cookies = match_cookies_to_url(url, cookies_map)
        return await super().afetch(url, cookies=cookies, warmup_url=warmup_url, do_warmup=do_warmup)
```
