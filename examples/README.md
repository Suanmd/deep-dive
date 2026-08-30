# Examples

> deep-dive 的可直接运行的示例脚本。

## 文件清单

| 文件 | 说明 |
|------|------|
| `quick_demo.sh` | 2-3 task 快速反馈，~30 秒完成 |
| `normal_demo.sh` | 7-8 task 默认深度，~10 分钟 |
| `full_demo.sh` | 14 task 全深度，~30 分钟 |

## 用法

所有 demo 脚本都需要：

```bash
# 1. 安装 deep-dive（见 installation.md）
pip install -r ../requirements.txt
pip install -e ..
playwright install chromium

# 2. 设置 Tavily API key（可选）
export TAVILY_API_KEY="tvly-..."

# 3. 跑 demo
./quick_demo.sh
```

输出在 `./demo-output/` 下。

## 何时用哪个

| 场景 | 用 |
|------|-----|
| 探索阶段 / 快速验证 query | `quick_demo.sh` |
| 章节素材采集（标准） | `normal_demo.sh` |
| 长篇报告（≥5 章节）/ 学术深度 | `full_demo.sh` |

## 自定义

复制 demo 脚本然后改 query / depth / output 路径：

```bash
cp quick_demo.sh my_research.sh
# 编辑 my_research.sh，把 query 改你自己的
chmod +x my_research.sh
./my_research.sh
```

或者直接在命令行：

```bash
deep-dive --query "我的研究主题" --depth normal --output ./my-research
```
