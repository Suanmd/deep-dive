#!/usr/bin/env bash
# quick_demo.sh — 快速反馈 demo（2-3 task，~30 秒）
#
# 适用场景：
#   - 探索阶段：还没决定要不要深入研究
#   - 时间敏感："5 分钟内给我一个 quick look"
#   - 测试管道是否正常工作
#
# 用法：
#   chmod +x quick_demo.sh
#   ./quick_demo.sh

set -euo pipefail

# 切到脚本所在目录的父目录（项目根）
cd "$(dirname "$0")/.."

# 主题：选一个中文 topic 演示本地语言扩展
QUERY="${QUERY:-中国新能源汽车 2026}"

echo "==> deep-dive quick demo"
echo "    query: $QUERY"
echo "    depth: quick (~30 秒)"
echo ""

deep-dive \
    --query "$QUERY" \
    --depth quick \
    --lang auto \
    --max-workers 2 \
    --output ./demo-output/quick

echo ""
echo "==> 完成！查看结果："
LATEST=$(ls -dt ./demo-output/quick/*/ 2>/dev/null | head -1)
if [ -n "$LATEST" ]; then
    echo "    报告: ${LATEST}report.md"
    echo "    元数据: ${LATEST}summary.json"
    echo ""
    echo "==> 报告预览（前 30 行）："
    head -30 "${LATEST}report.md" 2>/dev/null || echo "（无报告）"
fi
