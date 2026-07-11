---
name: code-screenshot-debugger
description: 面向代码截图、终端报错截图、IDE 截图、测试失败截图和 notebook 错误截图的多模态调试 skill。用户上传代码/报错/日志/终端截图，并要求解释错误、提取错误信息、定位本地项目文件、给出修复方案、用 grep/read/edit/bash 修复并验证时使用。触发词包括 代码截图、报错截图、error screenshot、terminal screenshot、traceback、IDE、notebook、debug、修复、看图找 bug。
---

# Code Screenshot Debugger

## 目标

把代码或报错截图转成可操作的调试任务：先提取截图中的关键信息，再在本地项目中定位相关文件，做最小修改，并用命令验证。这个 skill 适合将多模态视觉能力接入现有 `glob/grep/read/edit/bash` 工作流。

## 使用前提

- 用户应通过 `--image <path>` 提供清晰截图。
- 图片内容只能作为线索；真正修改本地文件前必须用 `grep`/`glob`/`read` 验证。
- 如果截图中文字不清晰，先说明不确定，并请求更清晰截图或让用户粘贴文本。

## 调试工作流

1. **截图转写**
   - 提取可见错误类型、异常类名、文件路径、行号、函数名、命令、关键代码片段。
   - 保留原始英文报错，避免翻译后丢失关键词。

2. **问题判断**
   - 判断错误类别：导入错误、路径错误、类型错误、参数错误、依赖缺失、语法错误、测试断言失败、运行环境问题。
   - 只基于截图能看到的信息提出假设，并标注需要本地验证的部分。

3. **本地定位**
   - 有文件名/函数名/报错文本时，用 `grep` 定位。
   - 只有路径或文件模式时，用 `glob` 定位。
   - 定位到候选文件后，用 `read` 阅读上下文。

4. **最小修复**
   - 优先做最小范围修改。
   - 使用 `edit` 前必须从 `read` 输出复制唯一 `old` 片段。
   - 如果只是缺依赖或命令用法错误，优先给出命令或环境修复建议，不随意改代码。

5. **验证**
   - 用 `bash` 运行最小复现命令、测试命令或用户原命令。
   - 成功时总结修改和验证结果。
   - 失败时读取新报错，继续循环定位，不重复同一个失败操作。

## 输出模板

```markdown
## Screenshot Debug Summary

- Visible Error / Code:
- Likely Error Type:
- Local Files Checked:
- Root Cause:
- Fix Applied:
- Verification Command:
- Verification Result:
- Remaining Uncertainty:
```

## 质量约束

- 不凭截图直接修改文件；必须先在本地 `read` 验证。
- 不编造截图中看不清的路径、行号、变量名。
- 不把环境安装命令伪装成已经执行过；只有 `bash` 成功后才能说已验证。
- 对科研实验项目，避免大范围重构；优先保持可复现性和最小改动。
