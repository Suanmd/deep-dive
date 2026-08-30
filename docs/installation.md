# Installation

> deep-dive 的安装步骤 + 验证清单。

## 前置要求

| 组件 | 最低版本 | 说明 |
|------|----------|------|
| Python | 3.10+ | 用 dataclass(slots=True) 需要 3.10 |
| pip | 21.0+ | PEP 517 build 需要 |
| Playwright Chromium | latest | 抓取核心 |
| mmx CLI (optional) | latest | 主搜索引擎 |
| Tavily API key (optional) | — | 兜底搜索引擎 |

## 步骤 1: 克隆 + 安装依赖

```powershell
git clone https://github.com/your-org/deep-dive.git
cd deep-dive

pip install -r requirements.txt
playwright install chromium
```

`requirements.txt` 内容：

```
trafilatura>=1.6.0
tavily-python>=0.3.0
playwright>=1.40.0
cloudscraper>=1.2.71
```

## 步骤 2: 安装包本体

```powershell
# 开发模式（推荐；代码改动即时生效）
pip install -e .

# 或者：仅使用，不改代码
pip install .

# 验证
python -m deep_dive --version
# 输出: deep-dive 1.0.0
```

## 步骤 3: 配置 API Key（可选）

### Tavily

```powershell
$env:TAVILY_API_KEY = "tvly-..."
# 备份 key（双 key 自动 fallback）
$env:TAVILY_API_KEY_BACKUP = "tvly-..."

# 持久化：写入 PowerShell profile
Add-Content $PROFILE "`n`$env:TAVILY_API_KEY='tvly-...'"
```

### mmx CLI

```powershell
# MiniMax Token Plan 用户
mmx auth login --api-key sk-...
mmx search query --q "test" --output json
```

非 MiniMax 用户可跳过——deep-dive 会自动只走 Tavily。

## 步骤 4: 配置 Cookie（可选）

只在你需要抓知乎/百度文库等登录墙站点时才需要：

```powershell
# 1. 复制模板
Copy-Item config/cookies.example.json config/cookies.json

# 2. 用 EditThisCookie 浏览器扩展导出 cookie
# 3. 粘贴到 config/cookies.json 对应的站点下
```

详见 [cookies.md](cookies.md)。

## 步骤 5: 验证安装

```powershell
# 最小冒烟测试（不需要任何 API key）
python -c "from deep_dive import Orchestrator, Config; cfg = load_config(); print('OK')"

# 完整流程测试（quick depth，~30 秒）
python -m deep_dive --query "test installation" --depth quick --no-tavily

# 期望输出: `[DONE] all complete!` + report.md 路径
```

## 安装验证清单

逐项打勾，全部 ✅ 才能确认安装成功：

- [ ] `python --version` ≥ 3.10
- [ ] `pip list | findstr deep-dive` 显示 1.0.0
- [ ] `python -m deep_dive --version` 输出 `deep-dive 1.0.0`
- [ ] `python -m pytest tests/ -q` 输出 `241 passed`
- [ ] `playwright --version` 输出浏览器版本号
- [ ] `python -m deep_dive --query "test" --depth quick` 生成 report.md（即使内容少）

## 故障排查

### `ModuleNotFoundError: No module named 'deep_dive'`

未安装包。运行 `pip install -e .`。

### `playwright install` 卡住

可能需要 `pip install playwright --upgrade` 后重试。

### `mmx: command not found`

mmx CLI 未安装。可以：
- 安装 mmx CLI（见上文）
- 或者直接用 `--no-tavily` + `--search-engine mmx` 跳过

### 中文输出乱码

PowerShell 默认 GBK。运行前：

```powershell
chcp 65001 > $null  # 切 UTF-8
```

## 开发依赖（贡献者用）

```powershell
pip install -r requirements-dev.txt
```

`requirements-dev.txt`：

```
-r requirements.txt
pytest>=7.4.0
pytest-cov>=4.1.0
pytest-asyncio>=0.23.0
pytest-mock>=3.12.0
ruff>=0.1.0
mypy>=1.7.0
```

### 本地开发循环

```powershell
# 代码改动后跑测试
pytest tests/ -q

# 类型检查
mypy src/deep_dive

# Lint
ruff check src tests
ruff format --check src tests
```

## Docker 安装（可选）

如果团队统一用 Docker，把这些加到 Dockerfile：

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy
RUN pip install deep-dive==1.0.0
ENV TAVILY_API_KEY=""
ENTRYPOINT ["deep-dive"]
```

跑：

```bash
docker run -e TAVILY_API_KEY=$KEY your-org/deep-dive --query "<query>"
```
