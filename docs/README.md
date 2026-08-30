# Documentation Index

> deep-dive 详细文档。按"先看哪一篇"排序。

## 🚀 上手 5 分钟

1. [installation.md](installation.md) — pip 安装 + Playwright Chromium
2. [usage.md](usage.md) — CLI 最常用 10 个用法
3. [engines.md](engines.md) — MMX vs Tavily 引擎选哪个

## 🧭 概念理解

4. [architecture.md](architecture.md) — 模块图 / 数据流
5. [query-templates.md](query-templates.md) — 5 类查询模板详解
6. [output-format.md](output-format.md) — `report.md` / `summary.json` 字段

## 🔧 进阶配置

7. [cookies.md](cookies.md) — Cookie 配置 + 导出指南
8. [engines.md](engines.md) — 自定义引擎 / 引擎注册
9. [url-candidates.md](url-candidates.md) — URL 候选池策略

## 🐛 排错 + 集成

10. [troubleshooting.md](troubleshooting.md) — 常见问题 + 排查
11. [workflow.md](workflow.md) — Agent 9 步工作流
12. [integration.md](integration.md) — 与 pdf_maker / create-science-book 协作

---

## 文档约定

- 代码示例优先用 PowerShell（Windows 是 deep-dive 的主要目标平台），POSIX shell 标注在 # comment 里。
- 路径用 `<workspace>/...` 占位符形式演示，跨平台兼容。
- 截图没有（项目无 UI），所以多靠 ASCII 图 + Markdown 表格。

## 快速跳转

| 问题 | 文档 |
|------|------|
| 怎么安装？ | [installation.md](installation.md) |
| 命令行怎么用？ | [usage.md](usage.md) |
| 卡皮观点是什么？ | [output-format.md](output-format.md#卡皮观点) |
| MMX 配额耗尽怎么办？ | [troubleshooting.md](troubleshooting.md#mmx-配额耗尽) |
| 怎么写自定义搜索引擎？ | [engines.md](engines.md#自定义引擎) |
| 跟 pdf_maker 怎么配合？ | [integration.md](integration.md) |
| 报告内容太少/太多？ | [query-templates.md](query-templates.md#深度档位) |
