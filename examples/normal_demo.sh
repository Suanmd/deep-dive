#!/usr/bin/env bash
# normal_demo.sh — 默认深度 demo（7-8 task，~10 分钟）
#
# 适用场景：
#   - 章节素材采集（标准做法）
#   - 单主题综合研究
#   - 写 1-3 章节的素材准备
#
# 用法：
#   chmod +x normal_demo.sh
#   ./normal_demo.sh

set -euo pipefail

# 切到脚本所在目录的父目录
cd "$(dirname "$0")/.."

# 主题：演示英文 + 中文双语搜索
QUERY="${QUERY:-<query> 1587}"

echo "==> deep-dive normal demo"
echo "    query: $QUERY"
echo "    depth: normal (~10 分钟)"
echo ""

deep-dive \
    --query "$QUERY" \
    --depth normal \
    --lang auto \
    --max-workers 2 \
    --output ./demo-output/normal

echo ""
echo "==> 完成！查看结果："
LATEST=$(ls -dt ./demo-output/normal/*/ 2>/dev/null | head -1)
if [ -n "$LATEST" ]; then
    echo "    报告:       ${LATEST}report.md"
    echo "    元数据:     ${LATEST}summary.json"
    echo ""
    echo "==> 摘要统计："
    python -c "
import json, sys
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
except Exception as e:
    print(f'    （解析失败: {e}）')
"
fi
