"""
最基本的 Agent 框架
- Client: 封装 LLM API 调用
- Tool System: 工具注册与执行
- Hook System: 4 阶段钩子（prompt_submit / pre_tool_use / post_tool_use / stop_loop）
- AgentLoop: 循环调用 LLM + 工具，直到给出最终回复
- Main: 主交互入口，管理记忆（对话历史）
"""

import json
import hashlib
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

# 从项目根目录加载 .env 文件
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

import requests
import yaml

# ============================================================
# 模块级状态
# ============================================================

# todo 任务列表 & 距上次更新经过的轮数
_todo_tasks: list[dict] = []
_turns_since_todo_update = 0

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


def todo_write(tasks_json: str) -> str:
    """创建或更新任务列表，解析 JSON 后打印到终端"""
    global _todo_tasks
    try:
        tasks = json.loads(tasks_json)
        if not isinstance(tasks, list):
            return "Error: 需要传入一个列表"
        _todo_tasks = tasks

        print("\n" + "=" * 40)
        print("  📋 任务列表")
        print("=" * 40)
        if not _todo_tasks:
            print("  （空）")
        else:
            for i, t in enumerate(_todo_tasks, 1):
                task = t.get("task", "")
                status = t.get("status", "pending")
                icon = {"pending": "⏳", "in_progress": "🔄", "completed": "✅"}.get(status, "⏳")
                print(f"  {i}. {icon} {task} [{status}]")
        print("=" * 40)
        return f"任务列表已更新，共 {len(tasks)} 项"
    except json.JSONDecodeError as e:
        return f"Error: JSON 解析失败 - {e}"


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

    registry.register(Tool(
        name="todo_write",
        description="创建或更新任务规划列表。接收 JSON 格式的任务数组，每个任务包含 task（描述）和 status（pending/in_progress/completed）。每次更新都会在终端打印当前任务列表。当开始新任务或完成/更新任务进度时，必须调用此工具告知用户。",
        parameters={
            "type": "object",
            "properties": {
                "tasks_json": {
                    "type": "string",
                    "description": "任务列表的 JSON 字符串，格式：[{\"task\": \"描述\", \"status\": \"pending\"}, ...]。status 可选值：pending（等待执行）、in_progress（正在执行）、completed（已完成）",
                },
            },
            "required": ["tasks_json"],
        },
        fn=todo_write,
    ))

    return registry


# ============================================================
# Skills 目录路径
# ============================================================

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def scan_skills() -> dict[str, dict]:
    """扫描 skills 目录，返回 {name: {name, description, content}}"""
    skills: dict[str, dict] = {}
    if not SKILLS_DIR.exists():
        return skills

    for skill_dir in SKILLS_DIR.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "skill.md"
        if not skill_file.exists():
            continue

        text = skill_file.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue

        frontmatter = yaml.safe_load(parts[1])
        name = frontmatter.get("name", skill_dir.name)
        description = frontmatter.get("description", "")
        content = parts[2].strip()

        skills[name] = {
            "name": name,
            "description": description,
            "content": content,
        }

    return skills


def load_skill(name: str) -> str:
    """加载指定 skill 的全文内容，注入到对话上下文"""
    all_skills = scan_skills()
    skill = all_skills.get(name)
    if skill is None:
        return f"Error: 技能 '{name}' 不存在。可用技能: {', '.join(all_skills.keys())}"
    return f"## 技能: {skill['name']}\n\n{skill['content']}"


# ============================================================
# 上下文压缩阈值
# ============================================================

TRANS_DIR = Path(__file__).resolve().parent / ".transcripts"
COMPACT_CHAR_THRESHOLD = 80000


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


# 内置 hook: 工具执行后打印到终端
def print_tool_use(tool_name: str, tool_args: dict, result: str, **kwargs):
    """在终端打印工具名称、参数和结果"""
    print(f"\n[工具] {tool_name}")
    print(f"  参数: {tool_args}")
    print(f"  结果: {result[:200]}{'...' if len(result) > 200 else ''}")


# 内置 hook: todo 提醒机制
def todo_reminder_reset(tool_name: str, **kwargs):
    """每次调用 todo_write 时重置提醒计数器"""
    global _turns_since_todo_update
    if tool_name == "todo_write":
        _turns_since_todo_update = 0


def todo_reminder_check(messages: list[dict], **kwargs):
    """每轮 LLM 调用前检查——连续 3 轮未更新 todo 则注入提醒"""
    global _turns_since_todo_update
    _turns_since_todo_update += 1
    if _turns_since_todo_update >= 4:
        messages.append({
            "role": "user",
            "content": "（提醒：已有几轮未更新任务列表，建议检查并更新任务进度）",
        })
        _turns_since_todo_update = 0


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

    def run(self, messages: list[dict], max_turns: int = 200) -> list[dict]:
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
                    # 安全审查可能通过 SecurityBlocked 阻止执行
                    try:
                        self.hooks.trigger(
                            HOOK_PRE_TOOL_USE,
                            tool_name=func_name,
                            tool_args=func_args,
                        )

                        # 执行工具
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
# 5b. 子 Agent — 作为母 Agent 的工具调用
# ============================================================

def run_subagent(task: str) -> str:
    """启动一个子 Agent 独立完成任务，返回结果摘要"""
    print(f"\n{'=' * 40}")
    print(f"  🤖 子 Agent 启动")
    print(f"  任务: {task}")
    print(f"{'=' * 40}")

    # 子 Agent 的工具集：包含 bash 和 todo_write，但不包含 subagent 自身（防递归）
    sub_tools = create_default_tools()

    # 子 Agent 的 hooks：安全审查 + 工具打印
    sub_hooks = HookSystem()
    sub_hooks.add_hook(HOOK_PRE_TOOL_USE, SecurityHook(project_dir=os.getcwd()))
    sub_hooks.add_hook(HOOK_POST_TOOL_USE, print_tool_use)

    sub_client = LLMClient()
    sub_agent = AgentLoop(sub_client, sub_tools, hook_system=sub_hooks)

    sub_messages = [
        {
            "role": "system",
            "content": (
                "你是一个子 Agent，负责独立完成委派给你的子任务。"
                "你可以使用 bash 工具执行 shell 命令，使用 todo_write 管理步骤。"
                "专注完成当前任务，完成后输出清晰的结果总结。"
                "不要询问用户，独立决策并执行。"
            ),
        },
        {"role": "user", "content": task},
    ]

    try:
        sub_messages = sub_agent.run(sub_messages, max_turns=30)
        result = sub_messages[-1].get("content", "(无输出)")
        print(f"\n{'=' * 40}")
        print(f"  🤖 子 Agent 完成")
        print(f"{'=' * 40}")
        return result
    except Exception as e:
        return f"子 Agent 执行出错: {e}"


# ============================================================
# 5c. 上下文压缩系统 — 五层压缩
# ============================================================

def _msg_chars(messages: list[dict]) -> int:
    """计算 messages 序列化后的字符数"""
    return len(json.dumps(messages, ensure_ascii=False, default=str))


def compact_trim_messages(messages: list[dict]) -> list[dict]:
    """第 1 层：消息数量裁切 — 保留前 3 条 + 后 47 条"""
    if len(messages) <= 50:
        return messages
    trimmed = list(messages[:3])
    trimmed.append({
        "role": "system",
        "content": f"(中间省略了 {len(messages) - 50} 条消息，已自动压缩)",
    })
    trimmed.extend(messages[-47:])
    return trimmed


def compact_tool_results(messages: list[dict]) -> None:
    """第 2 层：工具结果压缩 — 保留最近 3 轮工具结果，更早的用占位符替换"""
    # 找到所有轮次边界（assistant 含 tool_calls 的位置）及每条 tool_calls 对应的 tool 消息
    rounds: list[int] = []  # 每轮起始索引（assistant 消息）
    for i, m in enumerate(messages):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            rounds.append(i)

    if len(rounds) <= 3:
        return

    # 保留最近 3 轮，更早的轮次中的 tool 消息压缩
    for r_idx in rounds[:-3]:
        # 从 assistant 消息开始，找紧随的 tool 消息（直到下一条非 tool 消息）
        j = r_idx + 1
        while j < len(messages) and messages[j].get("role") == "tool":
            orig = messages[j].get("content", "")
            if len(orig) > 0:
                preview = orig[:100].replace("\n", " ")
                messages[j]["content"] = (
                    f"(工具结果已压缩: {preview}...) [可用 compact 工具恢复]"
                )
            j += 1


def compact_truncate_large(messages: list[dict]) -> None:
    """第 3 层：截断超大消息 — 最后一条 user 消息 > 200KB 时，存档 > 30KB 的消息"""
    last_user = None
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = m
            break
    if last_user is None:
        return

    content_bytes = len(last_user.get("content", "").encode("utf-8"))
    if content_bytes <= 200 * 1024:
        return

    TRANS_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())

    for i, m in enumerate(messages):
        text = m.get("content", "")
        if isinstance(text, str) and len(text.encode("utf-8")) > 30 * 1024:
            h = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
            fname = f"{ts}_{h}.txt"
            (TRANS_DIR / fname).write_text(text, encoding="utf-8")
            messages[i]["content"] = (
                text[:2000] + f"\n[...已存档至 .transcripts/{fname}]"
            )


def compact_summarize(messages: list[dict], client: "LLMClient") -> list[dict]:
    """第 4 层：LLM 摘要压缩 — 保存完整对话到 .transcripts/，用 LLM 生成摘要"""
    TRANS_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    archive_path = TRANS_DIR / f"{ts}_full.json"
    archive_path.write_text(
        json.dumps(messages, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\n[压缩] 完整对话已存档至 {archive_path}")

    summary_prompt = (
        "将以下对话历史总结为一段摘要，保留以下内容：\n"
        "1. current goal - 当前正在完成的目标\n"
        "2. key findings/decisions - 关键发现和决策\n"
        "3. files read/changed - 读取/修改的文件列表\n"
        "4. remaining work - 剩余工作\n"
        "5. user constraints - 用户给出的约束条件\n"
        "尽可能简洁，控制在 2000 字以内。\n\n"
        "对话历史：\n"
    )

    # 构建摘要请求（不传 tools）
    summary_messages = [
        {"role": "user", "content": summary_prompt},
    ]
    summary_messages.extend(messages)
    try:
        resp = client.chat_completion(summary_messages, tools=None, temperature=0.3)
        summary = resp["choices"][0]["message"].get("content", "")
    except Exception as e:
        summary = f"(摘要生成失败: {e})"

    # 构建精简消息：system prompt 原文 + 摘要 + 最后 10 条
    new_msgs = [messages[0]]  # system prompt
    new_msgs.append({
        "role": "system",
        "content": f"[对话摘要]\n{summary}",
    })
    new_msgs.extend(messages[-10:])
    print(f"[压缩] 消息从 {len(messages)} 条压缩为 {len(new_msgs)} 条")
    return new_msgs


def compact_emergency(messages: list[dict]) -> list[dict]:
    """第 5 层：紧急裁切 — 仅保留 system prompt + 简单摘要 + 最后 5 条"""
    new_msgs = [messages[0]]  # system prompt
    summaries = []
    for m in messages[1:-5]:
        role = m.get("role", "")
        content = m.get("content", "")
        if role in ("user", "assistant") and isinstance(content, str) and content:
            summaries.append(f"[{role}] {content[:200]}")
    summary_text = " | ".join(summaries[:20])
    new_msgs.append({
        "role": "system",
        "content": f"(紧急压缩) 对话摘要: {summary_text[:3000]}",
    })
    new_msgs.extend(messages[-5:])
    return new_msgs


# 模块级引用，供 compact_tool 闭包访问
_compact_refs: dict = {}


def compact_tool(**kwargs) -> str:
    """compact 工具 — 供 Agent 调用，触发第 4 层 LLM 摘要压缩"""
    messages = _compact_refs.get("messages")
    client = _compact_refs.get("client")
    if messages is None or client is None:
        return "Error: compact 工具未正确初始化"
    new_msgs = compact_summarize(messages, client)
    messages.clear()
    messages.extend(new_msgs)
    return "对话已压缩，摘要已生成，完整记录已存档。"


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

    # --- Skill 系统 ---
    all_skills = scan_skills()
    skills_list = ", ".join(all_skills.keys()) if all_skills else "(无)"
    # 生成技能列表描述，注入系统提示词
    skill_descriptions = "\n".join(
        f"  - {s['name']}: {s['description']}" for s in all_skills.values()
    ) if all_skills else "  暂无可用技能"

    tools.register(Tool(
        name="load_skill",
        description="加载指定技能的完整说明文档以获取详细指导。当需要使用某个已注册的技能（如代码审查、调试等）时，先调用此工具加载技能的完整文档。",
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": f"要加载的技能名称。可用技能: {skills_list}",
                },
            },
            "required": ["name"],
        },
        fn=load_skill,
    ))

    # 注册子 Agent 工具（放在 create_default_tools() 之外，因为子 Agent 不能递归调用自身）
    tools.register(Tool(
        name="subagent",
        description="启动一个子 Agent 独立完成指定的子任务。子 Agent 拥有 bash 和 todo_write 工具，可自主规划、执行并返回结果。适用于将复杂任务拆解后并行委派子 Agent 处理的场景。",
        parameters={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "委派给子 Agent 的具体任务描述，越详细越好",
                },
            },
            "required": ["task"],
        },
        fn=run_subagent,
    ))

    # 注册 compact 工具（供 Agent 主动触发摘要压缩）
    tools.register(Tool(
        name="compact",
        description="压缩对话历史以节省上下文空间。将完整对话存档后生成 LLM 摘要，保留当前目标、关键发现、已操作文件、剩余工作和用户约束等关键信息。",
        parameters={"type": "object", "properties": {}, "required": []},
        fn=compact_tool,
    ))

    # 设置 compact_tool 的模块级引用
    _compact_refs["client"] = client
    _compact_refs["messages"] = None  # 下面赋值

    # 创建安全审查钩子并注册到 pre_tool_use 阶段
    hooks = HookSystem()
    security = SecurityHook(project_dir=os.getcwd())
    hooks.add_hook(HOOK_PRE_TOOL_USE, security)

    # hook: 工具执行后打印到终端
    hooks.add_hook(HOOK_POST_TOOL_USE, print_tool_use)

    # hook: todo 提醒机制
    hooks.add_hook(HOOK_PRE_TOOL_USE, todo_reminder_reset)
    hooks.add_hook(HOOK_PROMPT_SUBMIT, todo_reminder_check)

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
                "你可以使用 todo_write 工具创建和更新任务规划列表，方便用户跟踪进度。"
                "你可以使用 subagent 工具将子任务委派给子 Agent 独立执行，适用于需要并行处理的多步骤任务。"
                "你可以使用 load_skill 工具加载特定技能的完整说明文档。"
                f"当前已注册的技能:\n{skill_descriptions}\n"
                "请根据任务需要自主决定何时使用工具。当任务完成时，给出清晰的总结。"
            ),
        }
    ]

    agent = AgentLoop(client, tools, hook_system=hooks)
    _compact_refs["messages"] = messages  # compact 工具需要访问 messages 和 client

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

        # --- 上下文压缩（第 1~3 层）---
        messages = compact_trim_messages(messages)
        compact_tool_results(messages)
        compact_truncate_large(messages)

        # --- 第 4 层：超过阈值则 LLM 摘要 ---
        if _msg_chars(messages) > COMPACT_CHAR_THRESHOLD:
            messages = compact_summarize(messages, client)

        # 执行 agent 循环
        try:
            messages = agent.run(messages)
            last_msg = messages[-1]
            print(f"\n{last_msg.get('content', '')}")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 413:
                print("\n[压缩] 上下文超限，执行紧急裁切...")
                messages = compact_emergency(messages)
                try:
                    messages = agent.run(messages)
                    last_msg = messages[-1]
                    print(f"\n{last_msg.get('content', '')}")
                except Exception as e2:
                    print(f"\n错误: {e2}")
                    messages.append({
                        "role": "assistant",
                        "content": f"执行出错: {e2}",
                    })
            else:
                raise
        except Exception as e:
            print(f"\n错误: {e}")
            messages.append({
                "role": "assistant",
                "content": f"执行出错: {e}",
            })


if __name__ == "__main__":
    main()
