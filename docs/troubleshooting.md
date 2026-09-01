# Troubleshooting

> 常见问题 + 排查清单。

## 进程退出码 1 但任务实际成功

**现象**：PowerShell 输出 `[Process exited with code 1]`，但 `report.md` 存在且内容完整。

**真正原因**：PowerShell 输出编码（GBK）与 Python stdout 编码（UTF-8）冲突，导致管道关闭时触发伪退出码。这是 PowerShell `Tee-Object` 伪信号问题。

**判断标准**：
1. 看文件系统：`ls <output>/<topic>__<run-id>/report.md` 是否存在
2. 看进程输出：是否出现 `[DONE] all complete!`
3. 看 `[DONE]` 后面的 summary 行数

**不要**：
- ~~直接看 exit code~~
- ~~因为 exit code = 1 就重跑~~

deep-dive **永远 exit 0**（即使内部失败），但 PowerShell 包装层可能注入伪信号。

## 报告内容空 / 极少

| 原因 | 排查 | 解决 |
|------|------|------|
| 所有 task 拦截 | 看 `summary.json` 的 `task_results`，看每个 task 的 `error` 字段 | 补 cookie / 换搜索引擎 |
| 关键词太泛 | query 过于宽泛（缺少限定词） | 重写为更具体的表述 |
| 单语言查询 | 中文主题只有中文搜索 | 加 `--lang=en` 或 `--lang=auto` |
| depth 太浅 | quick 不足以覆盖 | 改 `--depth=normal` 或 `full` |
| 网络代理问题 | 抓取全部超时 | 检查代理 / 重试 |
| quota 耗尽 | stderr 有 `[MMX-QUOTA]` 或 `[TAVILY-FALLBACK]` | 见下文 |

**dedup=0 急救**：先看 `summary.json` → `aggregated_summary.global_status`，再决定：

```powershell
# 急救命令：把 raw/ 下所有 .txt 拼成一个大文件
python -c "
import os
out = []
for root, _, files in os.walk('tmp/deep-dive/<topic>__<run-id>/raw'):
    for f in files:
        if f.endswith('.txt') and not f.endswith('_raw_all.txt'):
            out.append(open(os.path.join(root, f), encoding='utf-8', errors='ignore').read())
print('\n\n===\n\n'.join(out))
" > rescue.txt
```

## MMX 配额耗尽

**症状**：
- stderr 出现 `[MMX-QUOTA] quota exhausted: '<query>'`
- `summary.json` 里 `task_results[i].status == "quota_exceeded"`
- `report.md` 头部有 `[QUOTA 警告]`

**解决**（三选一）：

```bash
# 1. 等 4h 后重试（MMX 周期重置）
# 2. 减 depth（少调引擎）
deep-dive --query "test" --depth quick

# 3. 完全跳过 Tavily/MMX 链
deep-dive --query "test" --no-tavily
# （只剩 MMX 单引擎；配额耗尽时反而更糟）

# 推荐组合：
deep-dive --query "test" --depth quick --no-tavily
```

## Tavily 报错

```
[tavily] Error: 401 Unauthorized
```

解决：

```powershell
$env:TAVILY_API_KEY = "tvly-..."
# 或：
deep-dive --query "test" --search-engine mmx --no-tavily
```

`401` 是 key 无效；`429` 是 rate limit（每分钟 100 req）；`402` 是配额耗尽（自动切 KEY2）。

## MMX CLI 未安装

```
[MMX-ERR] mmx CLI not found on PATH; skipping '<query>'
```

解决：

```bash
# MiniMax Token Plan 用户
npm install -g mmx-cli
mmx auth login --api-key sk-...

# 非 MiniMax 用户：跳过 MMX，只用 Tavily
deep-dive --query "test" --search-engine tavily
```

## Cookie / CF 拦截

**症状**：抓取后 `looks_like_block_page()` 触发，`status=blocked`，写到 §4。

**排查**：

```powershell
# 看 raw/<task>/ 下 html 文件
ls tmp/deep-dive/<topic>__<run-id>/raw/*/*.html

# 找含拦截关键词的文件
Get-ChildItem -Path tmp/deep-dive/<topic>__<run-id>/raw -Recurse -Filter *.html |
  Select-String -Pattern "just a moment|cloudflare|verify you are human" |
  Select-Object -First 3
```

**解决**：
- 在 `config/cookies.json` 加对应域名 cookie（详见 [cookies.md](cookies.md)）
- 或：跳过该域名（用 `--exclude-site` 或在 Config 里改）

## 中文 URL 编码错

**症状**：URL 含中文字符，访问失败。

解决：`deep_dive.filters.canonical` 已经处理 URL 编码。如果还有问题，看：

```python
from urllib.parse import quote, urlparse
url = "https://example.com/路径/中文"
parts = urlparse(url)
encoded = quote(parts.path, safe="/")
print(f"{parts.scheme}://{parts.netloc}{encoded}{parts.query}")
```

## 输出目录中文名 GBK 乱码

**症状**：`<topic>` 含中文，输出目录名 PowerShell 显示乱码。

解决：
- **不要**用 PowerShell `Get-Content` 看路径
- 用 Python `open(path, encoding='utf-8')` 或资源管理器
- 文件系统本身是 UTF-16 / NTFS，**不是真的乱码**，只是 console 显示问题

## PowerShell 路径空格截断

**症状**：路径含空格，参数被截断。

解决：**始终**用双引号包路径：

```powershell
python -m deep_dive ... --output "<workspace>/tmp/deep-dive"
```

## 搜索太慢

**优化清单**：

```bash
# 1. 减并发（默认 2，Tavily rate-limit guard）
deep-dive --query "test" --max-workers 1

# 2. 改 depth
deep-dive --query "test" --depth quick

# 3. 检查哪个 task 最慢
deep-dive --query "test" --depth normal --debug
# 看 tmp/deep-dive/<topic>__<run-id>/debug/heartbeat.log
# 看哪个 task 一直没完成 → 那个 task 卡住了

# 4. 跳过特定 task（注：当前 CLI 无此 flag；需改源码）
```

## 同一主题重复跑覆盖输出

**现象**：相同 query 第二次跑覆盖了第一次结果。

解决：

```bash
# 用 --run-id 区分
deep-dive --query "test" --run-id "v1"
deep-dive --query "test" --run-id "v2"
```

否则默认时间戳（每次不同），但目录命名以 query 为主。

## URL 重试第一次超时/SSL

**现象**：第一次访问某 URL 超时或 SSL EOF，第二次常常成功。

`CrawlPipeline` 默认 `max_retries=1`，自动重试一次。如果还是失败：

```python
from deep_dive.crawler.pipeline import PipelineConfig

cfg = PipelineConfig(output_dir=..., main_query=..., max_retries=3)
```

## deep_dive 包找不到

```
ModuleNotFoundError: No module named 'deep_dive'
```

解决：

```powershell
pip install -e "<repo-path>/deep-dive"
```

或：

```powershell
$env:PYTHONPATH = "<repo-path>/deep-dive\src"
python -m deep_dive --query "test"
```

## 抓取退出码 1 但 report 成功

详见上方"进程退出码 1"条目——PowerShell 伪信号。

## 报告里卡皮观点写"数据不足"

**症状**：
```
## 🎀 卡皮观点 ...
> ⚠️ [EMPTY] 数据不足 — 本轮抓取 0 个独立 URL，成功 0。
```

这是**正常行为**：0 数据时不写 3 个观点（避免幻觉）。解决：

```bash
# 加 depth 跑
deep-dive --query "test" --depth full

# 或换 query
deep-dive --query "AI 大模型 综述 2026 article"  # 加限定词

# 或开 Tavily
deep-dive --query "test" --search-engine tavily
```

## 看具体哪个 task 失败

```powershell
# 看 summary.json
cat tmp/deep-dive/<topic>__<run-id>/summary.json | Select-String "status"
```

## 相关 URL 全进了 §4（低质页）

**症状**：正文 §3 是空的，所有 URL 都跑到 §4 低质页。

原因：URL 都被黑名单过滤了（`LOWQ_DOMAINS` / `SPAM_DOMAINS`）。

解决：
- 改 query 让搜索引擎返回不同站点
- 自定义过滤：`Config.depth_config` 或写自定义 `smart_filter_urls` 调用

## 看 stderr 日志诊断

```bash
deep-dive --query "test" --depth quick 2>&1 | tee debug.log
# 或者分开 stdout/stderr：
deep-dive --query "test" --depth quick > stdout.log 2> stderr.log
```

stderr 看 `[MMX-OK]` / `[TAVILY-OK]` / `[MMX-QUOTA]` / `[AUTO-RESCUE]` 等诊断 tag。

## 还有问题？

1. 跑 `deep-dive --query "test" --debug` 看 `debug/heartbeat.log`
2. 跑 `pytest tests/ -v` 看是否环境问题
3. 开 [GitHub Issue](https://github.com/Suanmd/deep-dive/issues) 附：
   - deep-dive 版本
   - 完整 stderr
   - `summary.json`
   - 重现命令
