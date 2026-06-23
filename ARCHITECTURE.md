# coding-agent.py 架构说明

## 整体架构

整个 Agent 分为 6 个模块，按顺序组织在单文件中：

```
┌─────────────────────────────────────────────────┐
│  ① LLMClient            LLM API 调用封装         │
├─────────────────────────────────────────────────┤
│  ② Tool / ToolRegistry  工具注册与执行系统        │
├─────────────────────────────────────────────────┤
│  ③ bash_execute          具体工具函数实现          │
├─────────────────────────────────────────────────┤
│  ④ HookSystem            4 阶段钩子系统            │
│     SecurityHook         安全审查（基于 Hook）     │
├─────────────────────────────────────────────────┤
│  ⑤ AgentLoop             LLM + 工具循环           │
├─────────────────────────────────────────────────┤
│  ⑥ main()                主交互入口，管理记忆      │
└─────────────────────────────────────────────────┘
```

---

## 模块详情

### ① LLMClient

**职责：** 封装对 LLM API 的 HTTP 调用。

```
LLMClient
├── __init__(model)         → 设置 model、base_url、api_key
└── chat_completion()       → POST /v1/chat/completions，返回 JSON
```

**被谁调用：** `AgentLoop.run()`

**关键设计：** OpenAI 兼容格式，支持随意切换兼容的 API（DeepSeek、OpenAI 等）。

---

### ② Tool / ToolRegistry

**职责：** 将普通 Python 函数包装成 LLM 可识别的工具格式。

```
Tool
├── __init__(name, desc, parameters, fn)
├── to_openai_tool()        → 生成 OpenAI function calling 格式
└── execute(arguments)      → 调用 fn(**arguments)

ToolRegistry
├── register(tool)
├── get(name)
├── list_openai_tools()     → 返回所有工具定义列表
└── execute(name, args)     → 按名称查找并执行工具
```

**被谁调用：**
- `create_default_tools()` 创建并注册工具
- `AgentLoop.run()` 通过 `list_openai_tools()` 获取工具定义传给 LLM，通过 `execute()` 执行工具

---

### ③ 工具函数实现（bash_execute / create_default_tools）

**职责：** 实现具体的工具功能，并注册到注册表。

```
bash_execute(command, timeout) → 执行 shell 命令，返回 stdout/stderr

create_default_tools() → 创建 ToolRegistry，注册 bash 工具
```

**被谁调用：**
- `create_default_tools()` 在 `main()` 中被调用，初始化工具集
- `bash_execute` 在 Tool.execute() 中被间接调用

**扩展方式：** 新增工具只需两步——写函数 + 在 `create_default_tools()` 中注册。

---

### ④ HookSystem + SecurityHook

#### HookSystem

**职责：** 可插拔的钩子管理器，在 Agent 运行的关键阶段插入自定义逻辑。

```
4 个阶段：
  HOOK_PROMPT_SUBMIT  → LLM 调用前
  HOOK_PRE_TOOL_USE   → 工具执行前
  HOOK_POST_TOOL_USE  → 工具执行后
  HOOK_STOP_LOOP      → 循环结束时

HookSystem
├── add_hook(phase, fn)      → 将函数注册到指定阶段
└── trigger(phase, **kwargs)  → 触发指定阶段的所有注册函数
```

#### SecurityHook

**职责：** 基于 HookSystem 实现的安全审查，注册到 `pre_tool_use` 阶段。

```
SecurityHook
├── __call__(tool_name, tool_args)  → Hook 入口
├── 第 1 层：硬拒绝                   → 匹配 hard_blocked_patterns 则抛 SecurityBlocked
├── 第 2 层：外部路径检查              → 检查命令是否操作项目目录之外
└── 第 3 层：用户确认                  → 询问用户 y/n
```

**被谁调用：**
- `HookSystem.trigger(HOOK_PRE_TOOL_USE)` 触发所有 pre_tool_use 钩子
- `main()` 中创建 SecurityHook 实例并通过 `hooks.add_hook()` 注册

---

### ⑤ AgentLoop

**职责：** 核心循环——反复调用 LLM → 解析响应 → 执行工具 → 继续循环，直到 LLM 返回纯文本。

```
AgentLoop
├── __init__(client, tool_registry, hook_system)
└── run(messages, max_turns=200) → 返回更新后的 messages
    流程：
      1. trigger(HOOK_PROMPT_SUBMIT)
      2. LLM.chat_completion(messages, tools)
      3. 解析响应，检查 tool_calls
      4. 如果有工具调用：
         a. trigger(HOOK_PRE_TOOL_USE)    ← 安全审查在此拦截
         b. tools.execute(name, args)
         c. trigger(HOOK_POST_TOOL_USE)
         d. 追加 tool 结果到 messages
         e. 回到步骤 1
      5. 无工具调用：
         a. trigger(HOOK_STOP_LOOP)
         b. 返回 messages
```

**关键设计：**
- **不管理记忆**——messages 由外部传入，loop 只消费和追加
- **不感知安全审查**——通过 Hook 接入，loop 只加了一层 `try/except SecurityBlocked`

---

### ⑥ main()

**职责：** 主交互入口，组装所有模块，管理对话记忆，驱动用户交互。

```
main()
├── 读取环境变量，初始化 LLMClient
├── 创建 ToolRegistry（create_default_tools）
├── 创建 HookSystem + 注册 SecurityHook + print_tool_use
├── 创建 AgentLoop
├── 初始化 messages（含 system prompt）
└── 交互循环：
    1. 读取用户输入
    2. 追加到 messages
    3. agent.run(messages)
    4. 输出最后一条助手消息
```

---

## 模块调用关系总图

```
┌──────────┐    创建       ┌──────────────┐
│  main()  │──────────────→│  LLMClient   │
│ (记忆管理)│              └──────────────┘
│          │    创建       ┌──────────────────┐
│          │──────────────→│  ToolRegistry     │
│          │              │  ├─ bash_execute   │
│          │              └──────────────────┘
│          │    创建       ┌──────────────────┐
│          │──────────────→│  HookSystem       │
│          │              │  ├─ SecurityHook  │
│          │              │  └─ print_tool_use│
│          │              └──────────────────┘
│          │    创建       ┌──────────────┐
│          │──────────────→│  AgentLoop    │
│          │              └──────────────┘
│          │                    │
│          │    agent.run()     │
│          │    ───────────────→│
│          │                    │
└──────────┘                    │
                                │  循环内：
                                │  ┌─→ LLMClient.chat_completion()
                                │  │    ↓
                                │  │  HookSystem.trigger(pre_tool_use)
                                │  │    ↓
                                │  │  ToolRegistry.execute()
                                │  │    ↓
                                │  │  HookSystem.trigger(post_tool_use)
                                │  └── (继续循环)
                                │       ↓
                                │  HookSystem.trigger(stop_loop)
                                │       ↓
                                │  返回 messages
                                │       ↓
                         main() 输出最终回复
```

---

## 数据流

```
用户输入
    │
    ▼
messages (list[dict])      ← main() 管理整个消息列表
    │
    ▼
LLMClient.chat_completion(messages, tools)
    │
    ▼
返回 response (含 tool_calls 或纯文本)
    │
    ├── 有 tool_calls → ToolRegistry.execute(name, args)
    │                       │
    │                       ▼
    │                   tool 结果 → 追加到 messages → 继续循环
    │
    └── 无 tool_calls → messages 返回给 main()
                            │
                            ▼
                        main() 打印最后一条 content
```

---

## 约定与原则

1. **记忆在 main 中管理**——AgentLoop 只消费和追加，不初始化、不清空
2. **扩展通过 Hook**——新增功能优先写 Hook 函数，注册到对应阶段，不改 Loop
3. **工具只需注册**——写工具函数 + 在 `create_default_tools()` 中注册即可
4. **安全是横切关注点**——SecurityHook 通过 Hook 接入，与核心逻辑解耦
