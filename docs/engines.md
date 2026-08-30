# Search Engines

> deep-dive 用什么引擎找 URL、怎么选、怎么写自定义引擎。

## 内置引擎一览

| 引擎 | 何时用 | API key | 配额 | 中文支持 |
|------|--------|---------|------|----------|
| **MMXEngine** | 默认主引擎 | 不需要（MiniMax Token Plan） | 取决于 plan | ★★★★★ |
| **TavilyEngine** | MMX 配额耗尽兜底 | `TAVILY_API_KEY` | 免费 1000/月 | ★★★★ |

## 引擎选择矩阵

```bash
# 默认（MMX 优先 + Tavily 兜底）
deep-dive --query "<query>" --search-engine auto
deep-dive --query "<query>"  # 等价

# 强制只走 MMX（省 Tavily 配额）
deep-dive --query "memory" --search-engine mmx --no-tavily
# 或：
deep-dive --query "memory" --search-engine auto --no-tavily

# 强制只走 Tavily（mmx CLI 未装时）
deep-dive --query "renaissance" --search-engine tavily
```

**经验法则**：
- 默认 `--search-engine auto`（不用写），让 deep-dive 自己选
- 看到 `[QUOTA]` tag 出现在 stderr → 加 `--no-tavily` 省配额
- 跑中文研究 → MMX 优先（中文信噪比通常更好）
- 跑英文研究 → Tavily 通常略胜（学术资源覆盖更全）

## MMX 引擎详解

### 原理

`mmx search query --q "<query>" --output json` 是 CLI 形式。deep-dive 用 subprocess 包装它，加超时保护（默认 35s）。

### 配额耗尽检测

stderr 包含以下任一关键词时判定为 quota：

```
"exceeds your plan", "exceeded your plan", "plan's set usage limit",
"plan usage limit", "quota exceeded", "quota_exceeded",
"insufficient_quota", "rate_limit_exceeded", "rate limit exceeded",
"rate-limit", "配额", "已达上限", "已达限制", "超出限制",
"超出额度", "超限"
```

→ 抛出 `SearchEngineQuotaError`，orchestrator 把整个 task 标为 `QUOTA_EXCEEDED`。

### 未安装 mmx

- `shutil.which("mmx")` 返回 None
- stderr 输出 `[MMX-ERR] mmx CLI not found`
- 静默返回 []（不抛错）
- 如果 Tavily 也没配 → 所有 task 走 `no_results` 路径，报告头 `[EMPTY 警告]`

## Tavily 引擎详解

### 双 key 自动 fallback

```
TAVILY_API_KEY      → KEY1（主）
TAVILY_API_KEY_BACKUP → KEY2（备用）
```

检测到 quota / forbidden / unauthorized / invalid / rate limit 时自动切 KEY2：

```
[TAVILY-FALLBACK] KEY1 quota exhausted → trying KEY2
[TAVILY-FALLBACK] used KEY2 (earlier key failed) for query='...' got 8 urls
```

两个 key 都失败 → 返回 []，不抛错（orchestrator 不知道是 quota 还是网络问题，统一走 `no_results`）。

### 配置方式

```powershell
# 临时
$env:TAVILY_API_KEY = "tvly-..."
$env:TAVILY_API_KEY_BACKUP = "tvly-..."

# 持久化（PowerShell profile）
Add-Content $PROFILE "`n`$env:TAVILY_API_KEY='tvly-...'"

# 用户级 YAML（推荐）
# 在 ~/.deep-dive/config.yaml 里设置：
# （注：API key 建议走环境变量，YAML 仅适合非敏感配置）
```

## 引擎底层接口

```python
class SearchEngine(abc.ABC):
    name: str = "abstract"

    def __init__(self, *, timeout_s: float = 30.0, topk_filter: int | None = None):
        self.timeout_s = timeout_s
        self.topk_filter = topk_filter

    def search(self, query: str, topk: int) -> list[SearchHit]:
        """同步搜索接口。带超时 + smart filter。"""

    async def asearch(self, query: str, topk: int) -> list[SearchHit]:
        """异步包装（asyncio.run_in_executor）。"""

    @abc.abstractmethod
    def _raw_search(self, query: str, topk: int) -> list[SearchHit]:
        """子类实现：实际的网络调用。"""

# 异常体系：
class SearchEngineError(Exception): ...
class SearchEngineQuotaError(SearchEngineError): ...  # 触发引擎切换
class SearchEngineTimeoutError(SearchEngineError): ...  # 触发 abort
```

## 自定义引擎

写一个新引擎很简单：

```python
# my_engines.py
from deep_dive.crawler.engines import SearchEngine, register_engine
from deep_dive.crawler.engines.base import SearchEngineQuotaError
from deep_dive.types import SearchHit

class BraveEngine(SearchEngine):
    name = "brave"

    def __init__(self, *, api_key: str, timeout_s: float = 30.0):
        super().__init__(timeout_s=timeout_s)
        self.api_key = api_key

    def _raw_search(self, query: str, topk: int) -> list[SearchHit]:
        import httpx
        if not self.api_key:
            return []
        try:
            resp = httpx.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": topk},
                headers={"X-Subscription-Token": self.api_key},
                timeout=self.timeout_s,
            )
            data = resp.json()
        except Exception as e:
            if "quota" in str(e).lower():
                raise SearchEngineQuotaError("brave quota") from e
            return []

        hits = []
        for item in data.get("web", {}).get("results", []):
            hits.append(SearchHit(
                url=item["url"],
                title=item.get("title", ""),
                snippet=item.get("description", ""),
                engine=self.name,
            ))
        return hits[:topk]

# 注册（程序启动时调用一次）
register_engine("brave", BraveEngine)
```

使用：

```python
from deep_dive import Orchestrator, load_config

orch = Orchestrator(
    load_config(),
    engines={
        "mmx": MyMMX(),            # 或内置 MMXEngine()
        "brave": BraveEngine(api_key="..."),
    },
)
result = orch.run(query="...")
```

CLI 用法（需要在 conftest 或 entrypoint 注册）：

```python
# entry.py
from deep_dive.crawler.engines import register_engine
from my_engines import BraveEngine
register_engine("brave", BraveEngine)
```

```bash
python entry.py  # 然后在脚本里 orch.run(search_engine="brave")
```

## 引擎配额对比

| 引擎 | 免费档 | 付费档起价 | 限速（每分钟） |
|------|--------|------------|----------------|
| MMX | MiniMax Token Plan | — | 取决于 plan |
| Tavily | 1000 search/月 | $30/月（10K search） | 100 req/min |
| Brave | 2000 query/月 | $3/月（5K query） | 1 req/s |

## 引擎注册中心（高级）

```python
from deep_dive.crawler.engines import get_engine, _ENGINE_REGISTRY

# 看现在注册了哪些
print(list(_ENGINE_REGISTRY.keys()))
# ['mmx', 'tavily']

# 动态构造
engine = get_engine("mmx", timeout_s=10.0)
engine.search("test", 5)
```

## 调试技巧

### 单独跑引擎

```python
from deep_dive.crawler.engines import MMXEngine, TavilyEngine

mmx = MMXEngine(timeout_s=10.0)
hits = mmx.search("test query", topk=5)
for h in hits:
    print(h.url, h.title)
```

### 看 ENGINE-AUDIT 日志

每次搜索引擎调用会输出：

```
[ENGINE-AUDIT] query='...' mode=auto mmx=15/12 tavily=8/6 merged=15
```

含义：`mmx=原始命中/过滤后` `tavily=原始命中/过滤后` `merged=去重后`。

mode 三种：`auto`（MMX 优先）/ `mmx-only` / `tavily-only`。

### 跑 tweak

```bash
# 把 timeout 改长（mmx CLI 卡死时用）
deep-dive --query "test" --no-report  # 不写 report，只看搜索日志

# 看 raw 目录（如果开了 --debug）
ls tmp/deep-dive/<topic>__<run-id>/debug/
# heartbeat.log:  每 10s 进度
# matrix.json:    完整搜索矩阵
```
