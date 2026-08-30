#!/usr/bin/env bash
# full_demo.sh — 全深度 demo（14 task，~30 分钟）
#
# 适用场景：
#   - 长篇报告（≥5 章节）
#   - 学术深度研究
#   - 完整选题（要详尽覆盖所有视角）
#
# 用法：
#   chmod +x full_demo.sh
#   ./full_demo.sh

set -euo pipefail

# 切到脚本所在目录的父目录
cd "$(dirname "$0")/.."

# 主题：演示学术 + 商业多视角
QUERY="${QUERY:-AI Agent 评估 benchmark 2026}"

echo "==> deep-dive full demo"
echo "    query: $QUERY"
echo "    depth: full (~30 分钟)"
echo ""

# --debug 模式落盘每步状态（便于排查）
deep-dive \
    --query "$QUERY" \
    --depth full \
    --lang auto \
    --max-workers 3 \
    --output ./demo-output/full \
    --debug

echo ""
echo "==> 完成！查看结果："
LATEST=$(ls -dt ./demo-output/full/*/ 2>/dev/null | head -1)
if [ -n "$LATEST" ]; then
    echo "    报告:       ${LATEST}report.md"
    echo "    元数据:     ${LATEST}summary.json"
    echo "    调试日志:   ${LATEST}debug/"
    echo ""
    echo "==> 摘要统计："
    python -c "
import json
try:
    s = json.load(open('${LATEST}summary.json', encoding='utf-8'))
    agg = s.get('aggregated_summary', {})
    print(f'    总 URL:     {agg.get(\"total_urls\", 0)}')
    print(f'    全局状态:   {agg.get(\"global_status\", \"unknown\")}')
    print(f'    任务数:     {len(s.get(\"task_results\", []))}')
    by_status = {}
    for t in s.get('task_results', []):
        by_status[t['status']] = by_status.get(t['status'], 0) + 1
    print(f'    状态分布:   {by_status}')
    print()
    print('    任务详情:')
    for t in s.get('task_results', []):
        print(f'      [{t[\"status\"]:>16}] {t[\"note\"]:<30} → {t.get(\"url_count\", 0)} URLs  ({t.get(\"duration_seconds\", 0):.1f}s)')
except Exception as e:
    print(f'    （解析失败: {e}）')
"
fi
