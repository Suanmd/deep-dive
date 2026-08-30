# 更新日志

## [1.0.0] — 2026-08-30

**deep-dive** 首次开源发布。

### 特性

- **多角度搜索矩阵** — 5 种查询模板（人文 / 技术 / 学术 / 新闻 / 商业）× 3 档深度（quick / normal / full）× 5 种视角变体（original / refined / critique / academic / comparative）。
- **双引擎策略** — MMX（主引擎，需外部 `mmx` CLI）+ Tavily（兜底，**N-key 依次自动 fallback**）。MMX 配额打爆后 Tavily 接力；Tavily 在多 key 池中按序轮换：第一个 key 报 quota/auth/network/timeout 即标记为 exhausted 切下一个，全部 exhausted 后 DuckDuckGo 最后兜底。
- **Plan 驱动模式** — 宿主 LLM 提供 `ResearchPlan` JSON，编排器直接消费 `variants` / `english_search_terms` / `target_sites`。宿主不传 plan 时 `auto_plan()` 自动生成最小方案。
- **站点定向** — `target_sites` 触发 `site:<domain>` 查询，走 Tavily 通道（原生支持 `site:` 操作符）。技术类查询默认打 arxiv / github / paperswithcode。
- **全栈抓取** — Playwright（Chromium headless）主 + CloudScraper（CF 高防）兜底；支持 cookie 注入、随机 UA、人类行为模拟、Baidu 站点 warm-up bypass。
- **Cookie 注入** — 可选 `config/cookies.json`（gitignored），按域名后缀匹配；模板见 `config/cookies.example.json`。
- **三阶段相关性检查** — 单字密度 + 核心实体覆盖 + 主实体首段检测；稳定拦截离题内容（如 aliyun menu 页匹配关键词却无核心实体）。
- **跨任务去重** — URL 去重 + 内容 SHA1 fingerprint 去重 + 5-gram Jaccard paraphrase 去重（同站豁免到 0.85）+ 段落 SHA1 rescue 拼救。极端情况（dedup=0）自动生成 `<topic>_raw_all.txt`。
- **结构化 Markdown 报告** — 4 段式（任务执行 / URL 来源 / 全文内容 / 元数据）+ 自动 Capy 摘要（主题聚类 + 关键引用 + 多空论据 + Top 5 长文）。低质页单独进附录。
- **配置与代码分离** — 全部可调值（深度档、超时、阈值、低质域名列表）落在 `config/defaults.yaml`，代码里没有硬编码。优先级 CLI > env > YAML > built-in default。
- **Windows 终端兼容** — 自动应用 UTF-8 输出 + console code page 检测，cp936 终端会看到 `chcp 65001` 提示。
- **MIT 协议** — 宽松许可，商用友好。

### 注意事项

- `mmx` CLI 是外部依赖（不打包）。如需 MMX 作为主引擎请单独安装；否则仅用 Tavily 即可。
- 配置文件、cookie、API key 都不进 git，详见 `SECURITY.md`。
