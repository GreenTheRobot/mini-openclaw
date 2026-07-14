#!/bin/bash
# 创建带时间戳的 txt 文件，最多创建 3 次
# 使用计数器文件来追踪已创建的文件数量

COUNTER_FILE="demo/.timestamp_counter"
MAX_FILES=3

# 读取当前计数
if [ -f "$COUNTER_FILE" ]; then
    COUNT=$(cat "$COUNTER_FILE")
else
    COUNT=0
fi

# 检查是否已达到最大次数
if [ "$COUNT" -ge "$MAX_FILES" ]; then
    echo "已达到最大创建次数 ($MAX_FILES)，不再创建新文件。"
    exit 0
fi

# 创建带时间戳的文件
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
FILENAME="demo/timestamp_${TIMESTAMP}.txt"
echo "Created at: $(date)" > "$FILENAME"
echo "File number: $((COUNT + 1))" >> "$FILENAME"

# 更新计数
echo $((COUNT + 1)) > "$COUNTER_FILE"

echo "已创建文件: $FILENAME (第 $((COUNT + 1))/$MAX_FILES 个)"
