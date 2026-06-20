# Hook 创建规范

## 概述

Hook 系统提供了一种**可插拔的机制**，允许你在 Agent 循环的特定阶段注入自定义逻辑。不需要修改 `AgentLoop` 的核心代码，只需要写一个 hook 函数，然后注册到对应的阶段。

---

## 整体结构

```
HookSystem                    ← hook 管理器
  ├─ add_hook(phase, fn)      ← 注册 hook
  └─ trigger(phase, **kwargs) ← 触发 hook

AgentLoop                     ← 循环只调用 trigger，不关心具体实现
  ├─ prompt_submit  ────── trigger → hook_fn(messages=...)
  ├─ pre_tool_use   ────── trigger → hook_fn(tool_name=..., tool_args=...)
  ├─ post_tool_use  ────── trigger → hook_fn(tool_name=..., tool_args=..., result=...)
  └─ stop_loop      ────── trigger → hook_fn(messages=...)
```

### 核心类说明

**`HookSystem`**（`coding-agent.py` 第 196-213 行）
- `_hooks` — 字典，key 是阶段名，value 是 hook 函数列表
- `add_hook(phase, fn)` — 将函数注册到指定阶段
- `trigger(phase, **kwargs)` — 遍历执行该阶段所有 hook，传入上下文数据

---

## 四个 Hook 阶段

| 阶段常量 | 触发时机 | 传入的 kwargs |
|---|---|---|
| `HOOK_PROMPT_SUBMIT` | LLM 调用之前 | `messages` |
| `HOOK_PRE_TOOL_USE` | 工具执行之前 | `tool_name`, `tool_args` |
| `HOOK_POST_TOOL_USE` | 工具执行之后 | `tool_name`, `tool_args`, `result` |
| `HOOK_STOP_LOOP` | 循环结束前 | `messages` |

---

## 两步创建一个 Hook

### 第 1 步：写 Hook 函数

hook 函数是一个普通的 Python 可调用对象（普通函数或 \_\_call\_\_ 类），通过 `**kwargs` 接收上下文参数。

```python
def log_hook(tool_name: str, tool_args: dict, **kwargs):
    """记录每次工具调用"""
    print(f"[日志] 执行工具: {tool_name}, 参数: {tool_args}")
```

**或者用类实现（需要带状态时用类）：**

```python
class RateLimitHook:
    """限制工具调用频率"""

    def __init__(self, max_per_minute: int = 10):
        self.max_per_minute = max_per_minute
        self.call_times: list[float] = []

    def __call__(self, tool_name: str, **kwargs):
        import time
        now = time.time()
        # 清理超过 1 分钟的记录
        self.call_times = [t for t in self.call_times if now - t < 60]
        if len(self.call_times) >= self.max_per_minute:
            raise RuntimeError(f"工具调用超限：每分钟最多 {self.max_per_minute} 次")
        self.call_times.append(now)
```

**规范：**
- 函数签名必须包含 `**kwargs`，以兼容未来可能新增的上下文参数
- 不需要的参数通过 `**kwargs` 忽略（如 `log_hook` 只接收 `tool_name` 和 `tool_args`，用 `**kwargs` 收掉其他）
- 一般不需要返回值（返回值被忽略）
- **如果需要阻止后续流程（如安全审查），抛出异常**

### 第 2 步：注册到 HookSystem

在 `main()` 中（或任何初始化位置）创建 `HookSystem` 实例，调用 `add_hook()`：

```python
from coding_agent import HookSystem, HOOK_PRE_TOOL_USE, AgentLoop, LLMClient

hooks = HookSystem()

# 注册普通函数
hooks.add_hook(HOOK_PRE_TOOL_USE, log_hook)

# 注册类实例
rate_limiter = RateLimitHook(max_per_minute=30)
hooks.add_hook(HOOK_PRE_TOOL_USE, rate_limiter)

# 创建 agent 时传入
agent = AgentLoop(client, tool_registry, hook_system=hooks)
```

---

## 完整示例：一个计时 Hook

```python
import time

def timing_hook(**kwargs):
    """记录 agent 循环各阶段耗时"""
    phase = "unknown"
    if "messages" in kwargs and "tool_name" not in kwargs:
        # 这是 prompt_submit 或 stop_loop，没有 tool_name
        print(f"[计时] 当前消息数: {len(kwargs.get('messages', []))}")
    elif "tool_name" in kwargs:
        print(f"[计时] 工具调用: {kwargs['tool_name']}")

# 注册到多个阶段
hooks.add_hook(HOOK_PROMPT_SUBMIT, timing_hook)
hooks.add_hook(HOOK_PRE_TOOL_USE, timing_hook)
```

---

## 安全审查 Hook（参考实现）

安全审查是 `pre_tool_use` 阶段的一个具体 hook 实现，展示了 **带状态的类 hook** 模式：

```python
class SecurityHook:
    """三层安全审查"""

    def __init__(self, project_dir: str = "."):
        self.project_dir = os.path.abspath(project_dir)
        self.hard_blocked_patterns = ["rm -rf /", "shutdown", ...]
        self.soft_risk_patterns = ["rm ", "mv ", "cp ", ...]

    def __call__(self, tool_name: str, tool_args: dict, **kwargs):
        # ↑ 签名：只取需要的参数，用 **kwargs 忽略其他的
        if tool_name != "bash":
            return

        command = tool_args.get("command", "")
        # ... 检查逻辑 ...
        raise SecurityBlocked("拒绝执行")
```

### 关键设计要点

1. `__call__` 方法让**类的实例可以作为 hook 函数**直接注册
2. `tool_name != "bash"` 提前返回——hook 本身决定是否要处理某个工具
3. 通过 `raise SecurityBlocked` 阻止工具执行（AgentLoop 捕获后将其转为正常 tool 响应）

---

## 注意事项

1. **`add_hook` 按注册顺序执行**，同一个阶段的多个 hook 依次触发
2. **异常传播**：如果 hook 抛异常且未被捕获，会中断整个 Agent 循环
3. **hook 不返回数据给 AgentLoop**，如需在 hook 间共享状态，用外部的变量或类实例
4. **不要在 hook 里修改 `messages` 的内容**（除非明确知道后果），消息管理由 `main()` 负责
5. **hook 只负责触发点的逻辑注入**，不要写业务逻辑在 hook 里——工具该做的事放到工具里
6. **不使用的 hook 可以不注册**，对性能无任何影响
