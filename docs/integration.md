# Integration with Other Skills

> deep-dive 作为"素材采集层"与下游工具协作：create-science-book、pdf_maker、自研 agent。

## 总览

```
deep-dive                          下游工具
━━━━━━━━━                          ━━━━━━━━━
report.md ──┐
            ├──► read_reports.py ──► g1-content.txt
raw/*.txt ──┘                       g2-content.txt
                                     ...
                                     ↓
                              章节 .tex 撰写
                                     ↓
                              verify_urls.py
                                     ↓
                              fix.py
                                     ↓
                              xelatex 编译
                                     ↓
                              章节 .pdf ✅
```

## 1. 与 create-science-book 协作

deep-dive 是写书技能的**素材采集层**：

```
create-science-book 接收写书请求
  ↓
对每个章节主题调用 deep-dive
  ↓
读取 report.md 作为素材
  ↓
章节正文 + 来源引用 + metadata → 完成写书
```

### 示例

```python
# 在 create-science-book 内部
def research_chapter(chapter_topic: str, chapter_outline: list[str]):
    """为单个章节做深度研究。"""
    cfg = load_config(overrides={"depth": "full"})
    orch = Orchestrator(cfg)
    result = orch.run(query=chapter_topic)

    if not result.report_path:
        raise RuntimeError(f"No report generated for {chapter_topic}")

    # 提取 report.md 全文
    report = result.report_path.read_text(encoding="utf-8")

    # 喂给 LLM 写章节
    chapter_draft = llm_generate_chapter(
        topic=chapter_topic,
        outline=chapter_outline,
        material=report,
        url_pool=extract_urls_from_report(report),
    )

    return chapter_draft
```

## 2. 与 pdf_maker 协作（端到端）

```
┌─────────────────────────────────────────────────────────────┐
│  Skill 1: deep-dive                                         │
│  输入：<主题> [--depth] [--lang] [--freshness]              │
│  输出：<output>/<主题>__<run-id>/report.md + raw/          │
│  必备参数：depth=normal, lang=auto                          │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Skill 2: read_reports.py                                   │
│  输入：deep-dive 输出目录                                    │
│  输出：g1-content.txt / g2-content.txt / ... / gN_urls.txt │
│  核心：re.search 提取 ## 3. 全文内容                       │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Skill 3: 主进程生成章节 tex                                │
│  输入：read_reports.py 输出的素材 + URL 池                  │
│  输出：chapter.tex（含 \section / tcolorbox / 三线表 / bibitem）│
│  关键：URL 必须从 verified URLs 池中取                     │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Skill 4: verify_urls.py                                    │
│  输入：chapter.tex                                          │
│  输出：URL 校验报告                                         │
│  3 轮迭代：第 1 轮 OK → 第 2 轮失败 URL → 第 3 轮改写    │
│  关键：urllib.parse.quote 处理中文 URL；HEAD→GET fallback │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Skill 5: fix.py（含预处理）                                │
│  输入：chapter.tex                                          │
│  输出：chX.tex + _tmp.tex（含 URL 下划线 escape + ctexrep）│
│  关键：含 verbatim 块保护 + PowerShell GBK 乱码修复       │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Skill 6: xelatex 编译（2 次）                              │
│  输入：_tmp.tex                                              │
│  输出：_tmp.pdf                                              │
│  关键：cross-ref 警告 + rerunfilecheck 警告正常            │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Skill 7: cleanup + 汇报                                   │
│  删除：_tmp.* / *.aux / *.log / *.out / *.py             │
│  复制：_tmp.pdf → chapter.pdf                              │
│  汇报：页数 / 字数 / URL 数 / 章节号                        │
└─────────────────────────────────────────────────────────────┘
```

## 章节级硬性写作规则

> 这些是**不能违反**的规则，违反会导致全章返工。

1. **章节标题层级统一** —— 不混用 `\section` 和 `\chapter`：
   - 单章独立编译：用 `\section`
   - 主控合并模式：用 `\section`（避免 `\chapter` 触发的文档类兼容问题）
   - 详见 pdf_maker skill 的 main.tex 关键配置

2. **每章必须有导读块** —— 用 `tcolorbox` 统一格式：
   ```latex
   \begin{tcolorbox}[colback=blue!4!white, colframe=blue!60!black, title=\textbf{本章导读}]
   ```

3. **每章必须有真实 URL 引用** —— 不能编造占位；从 `references/url-candidates.md` 候选池取

4. **每章必须有表格** —— booktabs 三线表（`\toprule / \midrule / \bottomrule`），不用 `\hline`

5. **每章最后一节必须有小结** —— 避免突兀结尾

6. **引用块前留空行 + `\vspace{1em}`** —— 避免正文与 thebibliography 撞版

7. **推荐每章有流程图 / 拓扑图** —— 但不强制（小章节可省）

## URL 校验→替换 SOP

`verify_urls.py` 校验失败后的替代流程：

```python
import re, urllib.request, ssl, time

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

tex_path = r'<chapter_path>/chapter.tex'
content = open(tex_path, 'r', encoding='utf-8').read()

urls = re.findall(r'\\url\{([^}]+)\}', content)
print(f'发现 {len(urls)} 个 URL')

ok, fail = 0, []
for i, url in enumerate(urls):
    url = url.strip().replace(r'\_', '_')
    for j in range(2):  # 重试 1 次
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, context=ctx, timeout=10)
            if resp.status == 200:
                ok += 1
                print(f'  [{i+1}/{len(urls)}] OK {resp.status} {url[:80]}')
                break
        except Exception as e:
            if j == 1:
                fail.append((url, str(e)[:60]))
                print(f'  [{i+1}/{len(urls)}] ERR {str(e)[:50]} {url[:60]}')
            else:
                time.sleep(0.3)

print(f'\n结果: {ok}/{len(urls)} OK')
if fail:
    print('\n失败URL:')
    for url, err in fail:
        print(f'  {err}: {url[:100]}')
```

URL 候选池策略见 [url-candidates.md](url-candidates.md)。

## 完整端到端示例

```python
# 一键：从 query 到 PDF
def query_to_pdf(query: str, chapter_num: int, output_dir: Path):
    """跑 deep-dive + read_reports + 写章节 tex + 编译。"""
    # Step 1: 深度研究
    cfg = load_config(overrides={"depth": "full"})
    orch = Orchestrator(cfg)
    result = orch.run(query=query, run_id=f"ch{chapter_num}")

    # Step 2: 提取素材
    raw_dir = result.report_path.parent / "raw"
    materials = extract_materials(raw_dir)  # g1-content.txt 等

    # Step 3: 写章节
    chapter_tex = write_chapter(
        topic=query,
        materials=materials,
        chapter_num=chapter_num,
    )

    # Step 4: 校验 URL
    chapter_tex = verify_urls(chapter_tex)  # 用 verify_urls.py 替换失败的 URL

    # Step 5: 编译
    pdf_path = compile_latex(chapter_tex, output_dir)
    return pdf_path

# 用法
pdf = query_to_pdf("<query>", chapter_num=14, output_dir=Path("./chapters/14"))
print(f"Generated: {pdf}")
```

## 主题分类 → depth 选择

| 主题类别 | 推荐 depth | run-id 命名 |
|---------|----------|------------|
| 政策法规 | quick | `政策-<主题>` |
| 技术综述 | normal | `技术-<主题>` |
| 企业案例 | normal | `案例-<公司>` |
| 白皮书/报告 | normal | `报告-<发布机构>` |
| 市场规模/排行 | normal | `市场-<主题>` |
| 对比/差异 | normal | `对比-<主题>` |
| 整体布局 | normal | `布局-<主题>` |
| 新兴/前沿 | full | `前沿-<主题>` |
| 学术研究 | full | `研究-<主题>` |
| 实战落地 | normal | `落地-<主题>` |

## 多章节批处理

```bash
# POSIX
for ch in 14 15 16; do
  deep-dive --query "第${ch}章 主题" --depth full --output ./chapters/ch${ch}
done

# PowerShell
14, 15, 16 | ForEach-Object {
  deep-dive --query "第${_}章 主题" --depth full --output "./chapters/ch$_"
}
```

每个章节独立目录，章节之间互不干扰。

## 缓存策略

如果同一章节跑两次，第二次会覆盖。如果想保留旧版本：

```bash
deep-dive --query "<query>" --run-id v1
deep-dive --query "<query>" --run-id v2  # 不覆盖 v1
```

## 错误处理

下游工具发现 `report.md` 异常时：

```python
from pathlib import Path
import json

def safe_extract_materials(output_dir: Path) -> dict:
    """从 deep-dive 输出安全提取素材。"""
    summary_file = output_dir / "summary.json"
    if not summary_file.exists():
        raise FileNotFoundError(f"summary.json 不存在: {summary_file}")

    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    global_status = summary.get("aggregated_summary", {}).get("global_status", "unknown")

    if global_status == "no_results":
        raise ValueError(
            f"deep-dive 跑出 0 URL，建议："
            f"1) 加 depth=full；2) 换 query；3) 检查引擎配额"
        )
    elif global_status == "quota_exceeded":
        raise RuntimeError(
            f"deep-dive 配额耗尽，建议："
            f"1) 等 4h；2) 加 --no-tavily；3) 切搜索引擎"
        )

    # global_status in {"success", "mixed"} → 继续
    return extract_materials(output_dir / "raw")
```
