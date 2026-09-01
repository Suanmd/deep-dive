# 更新日志

## [1.0.0]

首次开源发布。

### 核心能力

- **多角度搜索矩阵** — 5 类模板 × 3 档深度 × 5 种视角
- **双引擎策略** — MMX（主）+ Tavily（N-key 池化，按 quota / auth / network / timeout 任意失败自动切下一个）
- **Plan 驱动模式** — 宿主 LLM 提供 `ResearchPlan` JSON，编排器消费 `variants` / `english_search_terms` / `target_sites`
- **站点定向** — `target_sites` 发出 `site:<domain>` 查询
- **全栈抓取** — Playwright（Chromium headless）主 + CloudScraper（CF 高防）兜底
- **Cookie 注入** — `config/cookies.json`（gitignored）+ 模板 `config/cookies.example.json`
- **两阶段相关性检查** — 单字密度 + 核心实体命中率
- **跨任务去重** — URL 去重 + 段落 SHA1 指纹 + 5-gram Jaccard paraphrase 检测；dedup=0 时 auto-rescue 拼救 raw
- **4 段式 Markdown 报告** + **Capy 摘要**（主题聚类 + 关键引用 + 多空论据）
- **配置与代码分离** — 全部可调值在 `config/defaults.yaml`
- **Windows 终端兼容** — UTF-8 safe_print 统一来源
- **MIT 协议**
