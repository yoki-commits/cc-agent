"""
最基本的 Agent 框架
- Client: 封装 LLM API 调用
- Tool System: 工具注册与执行
- Hook System: 4 阶段钩子（prompt_submit / pre_tool_use / post_tool_use / stop_loop）
- AgentLoop: 循环调用 LLM + 工具，直到给出最终回复
- Main: 主交互入口，管理记忆（对话历史）
"""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

# 从项目根目录加载 .env 文件
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

import requests

# ============================================================
# 1. Client — 封装 LLM API 调用
# ============================================================

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")


class LLMClient:
    """OpenAI 兼容的 LLM 客户端"""

    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("MODEL_ID", "deepseek-chat")
        self.base_url = DEEPSEEK_BASE_URL
        self.api_key = DEEPSEEK_API_KEY

    def chat_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
    ) -> dict:
        """调用 LLM，返回完整响应"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools

        resp = requests.post(
            f"{self.base_url}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()


# ============================================================
# 2. Tool 系统 — 工具注册与执行
# ============================================================

class Tool:
    """将一个普通函数包装为 LLM 可调用的工具"""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict,
        fn: Callable[..., str],
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.fn = fn

    def to_openai_tool(self) -> dict:
        """生成 OpenAI 格式的 tool 定义"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def execute(self, arguments: dict) -> str:
        """执行工具并返回结果字符串"""
        result = self.fn(**arguments)
        return str(result)


class ToolRegistry:
    """工具注册表"""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_openai_tools(self) -> list[dict]:
        return [t.to_openai_tool() for t in self._tools.values()]

    def execute(self, name: str, arguments: dict) -> str:
        tool = self.get(name)
        if tool is None:
            return f"Error: tool '{name}' not found"
        return tool.execute(arguments)


# ============================================================
# 3. 工具函数实现 — bash
# ============================================================

def bash_execute(command: str, timeout: int = 30) -> str:
    """在本地执行 shell 命令并返回输出"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if output:
                output += "\n"
            output += result.stderr
        if result.returncode != 0:
            output += f"\n(exit code: {result.returncode})"
        return output if output else "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


# 创建默认工具列表
def create_default_tools() -> ToolRegistry:
    registry = ToolRegistry()

    registry.register(Tool(
        name="bash",
        description="执行 shell 命令。支持管道、重定向、多条命令链接（&&、||、;）。",
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令",
                },
                "timeout": {
                    "type": "integer",
                    "description": "超时时间（秒），默认 30",
                    "default": 30,
                },
            },
            "required": ["command"],
        },
        fn=bash_execute,
    ))

    return registry


# ============================================================
# 4. Hook 系统 — 4 阶段钩子
# ============================================================

# 可用 hook 阶段
HOOK_PROMPT_SUBMIT = "prompt_submit"   # 用户提交 prompt，LLM 调用前
HOOK_PRE_TOOL_USE  = "pre_tool_use"    # 工具执行前
HOOK_POST_TOOL_USE = "post_tool_use"   # 工具执行后
HOOK_STOP_LOOP     = "stop_loop"       # 循环结束（得到最终回复或超限）


class HookSystem:
    """可插拔的 hook 管理器"""

    def __init__(self):
        # 字典列表：每个阶段对应一个 hook 函数列表
        self._hooks: dict[str, list[Callable]] = {
            HOOK_PROMPT_SUBMIT: [],
            HOOK_PRE_TOOL_USE: [],
            HOOK_POST_TOOL_USE: [],
            HOOK_STOP_LOOP: [],
        }

    def add_hook(self, phase: str, hook_fn: Callable) -> None:
        """将 hook 函数注册到指定阶段"""
        if phase not in self._hooks:
            raise ValueError(f"未知 hook 阶段: {phase}")
        self._hooks[phase].append(hook_fn)

    def trigger(self, phase: str, **kwargs) -> None:
        """触发指定阶段的所有 hook，传入上下文数据"""
        for hook_fn in self._hooks.get(phase, []):
            hook_fn(**kwargs)


# ============================================================
# 4b. 安全审查系统 — 三层检查
# ============================================================

class SecurityBlocked(Exception):
    """安全审查拒绝执行，不向 LLM 暴露异常细节"""
    pass


class SecurityHook:
    """
    三层安全审查钩子，注册到 pre_tool_use 阶段。

    第 1 层 — 硬拒绝：高风险命令（删除系统、关机等），直接拦截。
    第 2 层 — 软风险：可能操作项目目录外的文件系统命令。
    第 3 层 — 用户确认：询问用户是否允许执行。
    """

    def __init__(self, project_dir: str = "."):
        self.project_dir = os.path.abspath(project_dir)

        # ── 第 1 层：硬拒绝模式 ──
        self.hard_blocked_patterns = [
            # 删除根目录 / 系统
            "rm -rf /", "rm -rf /*", "rm -rf --no-preserve-root",
            "rmdir /", "rmdir /s", "del /f /s",
            # 关机 / 重启
            "shutdown", "reboot", "halt", "poweroff",
            "shutdown -s", "shutdown -r", "shutdown /s", "shutdown /r",
            # 格式化 / 分区
            "format ", "mkfs", "fdisk", "parted", "mkswap",
            "format.", "format:",
            # 危险 dd
            "dd if=", "dd if=/dev/zero",
            # 系统权限
            "chmod 777 /", "chmod -R 777 /",
            "chmod 777 /", "chmod 7777 ",
            # 用户管理
            "userdel", "groupdel", "passwd", "usermod",
            "net user ", "net localgroup ",
        ]

        # ── 第 2 层：软风险模式（修改文件系统的操作）──
        self.soft_risk_patterns = [
            "rm ", "rmdir ", "del ", "rd ", "erase ",
            "mv ", "move ", "rename ", "ren ",
            "cp ", "copy ", "xcopy ", "robocopy ",
            "chmod ", "chown ", "attrib ", "cacls ", "icacls ",
            "mkdir ", "md ",
            "dd ",
            "truncate ", "fallocate ",
            "echo.>", "echo >", "echo>>", "type nul>",
        ]

    def __call__(self, tool_name: str, tool_args: dict, **kwargs):
        """Hook 入口 — 作为 pre_tool_use 阶段函数注册"""
        if tool_name != "bash":
            return

        command = tool_args.get("command", "")

        # ── 第 1 层：硬拒绝 ──
        for pattern in self.hard_blocked_patterns:
            if pattern.lower() in command.lower():
                raise SecurityBlocked(
                    f"⛔ 安全审查：高风险命令被拒绝（命中了禁止模式: {pattern}）"
                )

        # ── 第 2 层：检查是否操作项目目录外的路径 ──
        outside_paths = self._find_outside_paths(command)
        if not outside_paths:
            # 不涉及外部路径，软风险模式也忽略
            return

        # ── 第 3 层：用户确认 ──
        print(f"\n[安全审查] 以下命令可能操作项目目录外的文件：")
        print(f"  命令: {command}")
        print(f"  涉及外部路径: {', '.join(outside_paths)}")
        while True:
            resp = input("  是否允许执行？(y/N): ").strip().lower()
            if resp == "y":
                print("  → 用户允许，继续执行。")
                return
            elif resp in ("n", ""):
                raise SecurityBlocked(
                    f"⛔ 安全审查：用户拒绝执行操作外部路径的命令"
                )

    def _find_outside_paths(self, command: str) -> list[str]:
        """找出命令中指向项目目录外的路径"""
        found: list[str] = []

        # 提取被引号括起来的路径、或明显是路径的 token
        # 匹配：引号路径、以 .\\ ..\\ / \\ 开头的、含盘符的
        path_tokens = re.findall(
            r'["\']((?:[a-zA-Z]:)?[\\/][^"\'<>|;]*?)["\']'
            r'|((?:\.\.?[\\/])[^\s;"\'<>|&]+)'
            r'|((?:[a-zA-Z]:[\\/])[^\s;"\'<>|&]+)',
            command,
        )
        for groups in path_tokens:
            token = next(p for p in groups if p)  # 非空匹配组
            token = token.strip("\"'")
            try:
                full = os.path.abspath(os.path.join(self.project_dir, token))
                common = os.path.commonpath([full, self.project_dir])
                if common != self.project_dir:
                    found.append(token)
            except (ValueError, OSError):
                continue

        return found


# ============================================================
# 5. AgentLoop — 循环调用 LLM + 工具执行
# ============================================================

class AgentLoop:
    """
    与 LLM 多轮交互，直到模型返回纯文本（不再调用工具）。
    本身不管理记忆，由外部传入完整 messages。
    """

    def __init__(
        self,
        client: LLMClient,
        tool_registry: ToolRegistry,
        hook_system: HookSystem | None = None,
    ):
        self.client = client
        self.tools = tool_registry
        self.hooks = hook_system or HookSystem()

    def run(self, messages: list[dict], max_turns: int = 20) -> list[dict]:
        """
        执行 agent 循环。
        参数:
            messages: 完整的对话历史（外部管理记忆）
            max_turns: 最大工具调用轮数
        返回:
            更新后的 messages（追加了助手回复）
        """
        openai_tools = self.tools.list_openai_tools()

        for _ in range(max_turns):
            # --- hook: prompt_submit（LLM 调用前）---
            self.hooks.trigger(HOOK_PROMPT_SUBMIT, messages=messages)

            response = self.client.chat_completion(
                messages=messages,
                tools=openai_tools if openai_tools else None,
            )
            choice = response["choices"][0]
            message = choice["message"]

            # 将助手消息追加到对话
            messages.append(message)

            # 检查是否有工具调用
            if tool_calls := message.get("tool_calls"):
                for tc in tool_calls:
                    func_name = tc["function"]["name"]
                    try:
                        func_args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        func_args = {}

                    # --- hook: pre_tool_use（工具执行前）---
                    self.hooks.trigger(
                        HOOK_PRE_TOOL_USE,
                        tool_name=func_name,
                        tool_args=func_args,
                    )

                    # 执行工具（安全审查可能通过 SecurityBlocked 阻止执行）
                    try:
                        result = self.tools.execute(func_name, func_args)
                    except SecurityBlocked as e:
                        result = str(e)

                    # --- hook: post_tool_use（工具执行后）---
                    self.hooks.trigger(
                        HOOK_POST_TOOL_USE,
                        tool_name=func_name,
                        tool_args=func_args,
                        result=result,
                    )

                    # 将工具结果作为 tool 角色消息追加
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })
                # 继续循环让模型处理工具结果
                continue

            # --- hook: stop_loop（循环即将结束）---
            self.hooks.trigger(HOOK_STOP_LOOP, messages=messages)

            # 没有工具调用 → 得到最终回复，结束循环
            return messages

        # 超过最大轮数，追加一条提示
        messages.append({
            "role": "assistant",
            "content": "(已达到最大工具调用轮数，请简化你的任务或重试)",
        })
        self.hooks.trigger(HOOK_STOP_LOOP, messages=messages)
        return messages


# ============================================================
# 6. 主交互入口 — 管理记忆
# ============================================================

def main():
    """主交互入口：管理对话记忆，驱动 AgentLoop"""
    api_key = DEEPSEEK_API_KEY
    if not api_key:
        print("错误：请设置环境变量 DEEPSEEK_API_KEY")
        return

    client = LLMClient()
    tools = create_default_tools()

    # 创建安全审查钩子并注册到 pre_tool_use 阶段
    hooks = HookSystem()
    security = SecurityHook(project_dir=os.getcwd())
    hooks.add_hook(HOOK_PRE_TOOL_USE, security)

    print("=" * 50)
    print("  Agent 交互终端（输入 'exit' 退出）")
    print("=" * 50)

    # 记忆（对话历史）在主入口中维护
    # agent loop 不触碰 messages 的 lifecycle，只消费和追加
    messages: list[dict] = [
        {
            "role": "system",
            "content": (
                "你是一个有用的 AI 助手。你可以使用 bash 工具在本地执行 shell 命令来帮助用户。"
                "请根据任务需要自主决定何时使用工具。当任务完成时，给出清晰的总结。"
            ),
        }
    ]

    agent = AgentLoop(client, tools, hook_system=hooks)

    while True:
        try:
            user_input = input("\n>>> ")
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            print("再见！")
            break

        # 追加用户消息到记忆
        messages.append({"role": "user", "content": user_input})

        # 执行 agent 循环（内部会追加助手消息和工具消息到 messages）
        try:
            messages = agent.run(messages)
            # 输出最后一条助手消息
            last_msg = messages[-1]
            print(f"\n{last_msg.get('content', '')}")
        except Exception as e:
            print(f"\n错误: {e}")
            # 出错时把错误消息加入记忆，对话可以继续
            messages.append({
                "role": "assistant",
                "content": f"执行出错: {e}",
            })


if __name__ == "__main__":
    main()
