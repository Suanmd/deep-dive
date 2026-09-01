# 贡献指南（中文版）

感谢你对 deep-dive 感兴趣！本文档说明如何搭建开发环境、跑测试、提交干净的 PR。

> 英文原文见 [CONTRIBUTING.en.md](#)（如有）或参考原仓库同步内容。
> 本文档遵循中文表达习惯；如有歧义以英文为准。

---

## 📋 行为准则

本项目遵循 [Contributor Covenant 行为准则](CODE_OF_CONDUCT.md)（v2.1）。
参与即表示你同意遵守该准则。

---

## 🧰 开发环境搭建

### 前置依赖

- Python 3.10+
- Git
- （可选）Tavily API key — 跑引擎集成测试时需要
- （可选）`PATH` 上的 `mmx` CLI — 跑 MMX 引擎测试时需要

### 首次配置

```bash
# 克隆你的 fork
git clone https://github.com/Suanmd/deep-dive.git
cd deep-dive

# 建虚拟环境
python -m venv .venv
source .venv/bin/activate  # POSIX
# 或： .venv\Scripts\activate     # Windows

# 装运行时 + 开发依赖
pip install -r requirements-dev.txt
playwright install chromium

# editable 安装（让 `python -m deep_dive` 工作）
pip install -e .

# 自检
pytest --collect-only
ruff check src tests
```

---

## 🧪 测试

我们用 **pytest**。测试在 `tests/` 下，结构镜像源码布局。

```bash
# 全部测试
pytest

# 单元测试（不打网络）
pytest tests/unit

# 集成测试
pytest tests/integration

# 带覆盖率
pytest --cov=deep_dive --cov-report=term-missing

# 单文件
pytest tests/unit/test_canonical.py -v

# 跳过慢测试 / 网络测试
pytest -m "not slow and not network"
```

新增测试时请遵守：

- 单元测试**不能**打网络（mock 引擎 / 用 fixture）。
- 集成测试如果打网络，标记 `@pytest.mark.network`。
- 新模块至少在 `tests/unit/` 下加一个测试文件。
- 新代码覆盖率 ≥ 80%。

---

## 🎨 代码风格

- **[ruff](https://github.com/astral-sh/ruff)** — lint + import 排序
- **[mypy](https://mypy.readthedocs.io/)** — 静态类型检查
- **类型注解**：所有公开函数 / 方法必须有 type hints
- **Docstring**：模块和类用 Google style；短小 helper 可单行

提交前跑：

```bash
ruff check src tests
ruff format --check src tests
mypy src/deep_dive
```

---

## ⚙️ 配置纪律

deep-dive 严格遵守一条规则：**用户可调值写在 `config/` 文件里，不在 Python 代码里**。

当你发现自己想在 `src/deep_dive/` 里写硬编码列表、阈值、路径时——停下来，
放到 `config/defaults.yaml` 里。通过 `Config` 属性运行时读回。

---

## 📁 模块布局约定

| 新增内容 | 放哪里 |
|---------|--------|
| 新搜索引擎（Brave / Serper 等） | `src/deep_dive/crawler/engines/` |
| 新 fetcher（httpx / requests 等） | `src/deep_dive/crawler/fetchers/` |
| 新 URL 过滤 / canonical 步骤 | `src/deep_dive/filters/` |
| 新报告 section | `src/deep_dive/reporting/` |
| 新 CLI flag | 编辑 `src/deep_dive/cli.py`（argparse 保持精简） |
| 新配置键 | 同时编辑 `config/defaults.yaml` 和 `src/deep_dive/config.py` |
| 新文档页 | `docs/<topic>.md` + 在 `docs/README.md` 挂链接 |

模块保持小（< 400 LOC）。超了拆分。

---

## 🔀 PR 流程

1. **Fork 仓库**并创建功能分支：
   ```bash
   git checkout -b feat/my-new-feature
   ```

2. **改代码**，遵循上面的代码风格。

3. **加测试**覆盖新功能。

4. **更新文档**（如果你改了 CLI flag、配置键、公开 API）。

5. **更新 `CHANGELOG.md`** 在 "Unreleased" 段：
   ```markdown
   ## [Unreleased]
   ### Added
   - 新增 Brave 搜索引擎，对应 `--search-engine=brave`
   ```

6. **跑完整检查套件**：
   ```bash
   pytest
   ruff check src tests
   ruff format --check src tests
   mypy src/deep_dive
   bandit -r src/deep_dive -c pyproject.toml
   pip-audit -r requirements.txt --no-deps --strict
   ```

   CI 上的 4 个 job（lint / typecheck / security / test）在
   `.github/workflows/ci.yml` 跑同一套。

7. **Commit**，信息要描述清楚：
   ```
   feat(engines): add Brave search engine

   Implements a new search backend behind --search-engine=brave.
   Falls back gracefully when BRAVE_API_KEY is unset.

   Tests: tests/unit/test_brave_engine.py
   Docs: docs/engines.md
   ```

8. **Push 并开 PR** —— 填好 PR 模板。

9. **响应 review 反馈** —— 如有要求，合并前 squash commit。

---

## 🐛 报告 Bug

开 issue 时附上：

- `deep-dive --version`（或 `python -m deep_dive --version`）
- Python 版本和操作系统
- 完整命令（最好带 `--debug`）
- `<output_dir>/<topic>__<run-id>/summary.json` 的输出（**先脱敏 URL！**）
- 期望行为 vs 实际行为

---

## 💡 功能请求

开 issue 时附上：

- 用例（你想达成什么目标？）
- 提议的 API / CLI 形式
- 是否愿意自己实现？

---

## 📜 协议

贡献即表示你同意你的贡献按 [MIT 协议](LICENSE) 授权。