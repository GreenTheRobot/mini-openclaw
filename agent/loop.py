"""ReAct 主循环（Agent 的心脏）。

  while 没到最终答复:
      assistant = backend.chat(messages, tools)      # 模型这一步：思考 or 调工具
      if assistant 有 tool_calls:
          for call in tool_calls:
              obs = registry.get(call.name).run(**call.arguments)   # 执行工具
              messages.append(tool_result(obs))                     # 注入 observation
      else:
          return assistant.content                                 # 最终答复

Day5 你要把下面的 run() 真正实现出来（Day6 随工具集扩展完善）。骨架已给出结构与防呆上限。
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Callable

from backend.multimodal import image_block

from agent.context import maybe_compact, truncate_observation
from tools.base import ToolRegistry
from . import permissions


class AgentLoop:
    def __init__(self, backend: Any, registry: ToolRegistry, system_prompt: str,
                 max_turns: int = 20, workdir: str | Path | None = None,
                 auto_approve: bool = False,
                 confirm_callback: Callable[[str, dict[str, Any], permissions.PermissionDecision], bool] | None = None):
        self.backend = backend
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_turns = max_turns          # 防死循环：硬上限
        self.workdir = Path(workdir or os.getcwd()).resolve()
        self.auto_approve = auto_approve
        self.confirm_callback = confirm_callback

    def _run_tool(self, tool: Any, arguments: dict[str, Any]) -> str:
        old_cwd = os.getcwd()
        os.chdir(self.workdir)
        try:
            return tool.run(**arguments)
        finally:
            os.chdir(old_cwd)

    def run(self, user_task: str, image_paths: list[str] | None = None) -> str:
        user_content: Any = user_task
        if image_paths:
            user_content = [{"type": "text", "text": user_task}]
            user_content.extend(image_block(path) for path in image_paths)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ]
        for turn in range(self.max_turns):
            tools = self.registry.schemas() if getattr(self.backend, "supports_tools", True) else []
            assistant = self.backend.chat(messages, tools=tools)
            messages.append({"role": "assistant",
                             "content": assistant.get("content", ""),
                             "tool_calls": assistant.get("tool_calls", [])})

            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                return assistant.get("content", "")

            for call in tool_calls:
                call_name = call.get("name", "")
                arguments = call.get("arguments") or {}
                tool = self.registry.get(call_name) # 解析工具
                if tool is None:
                    obs = f"错误：未知工具 {call_name}"
                elif not isinstance(arguments, dict):
                    obs = f"错误：工具 {call_name} 的 arguments 必须是对象"
                else:
                    try:
                        decision = permissions.check(call_name, arguments, self.workdir) # 检查权限
                        if decision.verdict == "deny":
                            obs = f"[权限层] 拒绝：{decision.reason}"
                        elif decision.verdict == "confirm":
                            approved = self.auto_approve
                            if not approved and self.confirm_callback is not None:
                                approved = self.confirm_callback(call_name, arguments, decision)
                            if approved:
                                obs = self._run_tool(tool, arguments)
                            else:
                                obs = (
                                    f"[权限层] 未获确认：{call_name}({arguments})\n"
                                    f"原因：{decision.reason}\n"
                                    "已拦截且未执行。"
                                )
                        else:
                            obs = self._run_tool(tool, arguments)
                    except Exception as e:
                        obs = f"工具 {call_name} 执行出错：{e}"
                messages.append({"role": "tool", "name": call_name,
                                 "tool_call_id": call.get("id") or call_name,
                                 "content": truncate_observation(str(obs))})

            messages = maybe_compact(messages, self.backend)

        return "[达到最大轮数上限，未完成任务]"
