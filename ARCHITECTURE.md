# coding-agent.py 架构说明

## 整体架构

整个 Agent 分为 7 个模块，按顺序组织在单文件中：

```
┌─────────────────────────────────────────────────┐
│  ① LLMClient            LLM API 调用封装         │
├─────────────────────────────────────────────────┤
│  ② Tool / ToolRegistry  工具注册与执行系统        │
├─────────────────────────────────────────────────┤
│  ③ 工具函数              bash / todo_write       │
├─────────────────────────────────────────────────┤
│  ④ Skill 系统            技能扫描与加载           │
├─────────────────────────────────────────────────┤
│  ⑤ HookSystem            4 阶段钩子系统            │
│     SecurityHook         安全审查（基于 Hook）     │
│     print_tool_use       工具执行打印              │
│     todo_reminder        任务更新提醒              │
├─────────────────────────────────────────────────┤
│  ⑥ AgentLoop             LLM + 工具循环           │
│     SubAgent             子 Agent（作为工具）       │
├─────────────────────────────────────────────────┤
│  ⑦ main()                主交互入口，管理记忆      │
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

**被谁调用：** `AgentLoop.run()`、`run_subagent()`

**关键设计：** OpenAI 兼容格式，支持切换兼容的 API（DeepSeek、OpenAI 等）。model 和 base_url 从 `.env` 文件读取。

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
- `create_default_tools()` 创建并注册工具（bash、todo_write）
- `main()` 中额外注册 subagent 和 load_skill
- `AgentLoop.run()` 通过 `list_openai_tools()` 获取工具定义传给 LLM，通过 `execute()` 执行工具

**当前已注册工具：**

| 工具名 | 函数 | 注册位置 |
|---|---|---|
| `bash` | `bash_execute` | `create_default_tools()` |
| `todo_write` | `todo_write` | `create_default_tools()` |
| `subagent` | `run_subagent` | `main()` |
| `load_skill` | `load_skill` | `main()` |

---

### ③ 工具函数实现

**职责：** 实现具体的工具功能。

#### bash_execute
执行 shell 命令，返回 stdout/stderr。

#### todo_write（任务规划系统）
接收 JSON 格式的任务数组，存储到模块级状态 `_todo_tasks`，终端打印格式化任务看板。

```
任务状态：
  pending     — ⏳ 等待执行
  in_progress — 🔄 正在执行  
  completed   — ✅ 已完成
```

**模块级状态：**
- `_todo_tasks: list[dict]` — 当前任务列表
- `_turns_since_todo_update: int` — 距上次更新经过的轮数

---

### ④ Skill 系统

**职责：** 管理可加载的技能文档。技能存放在 `skills/` 目录下，每个子目录一个技能，含 `skill.md` 文件（YAML 头 + Markdown 正文）。

```
skills/
├── code-review/
│   └── skill.md     ← name + description (YAML) + content (Markdown)
└── debugger/
    └── skill.md
```

```
scan_skills()                     → 扫描 skills/ 目录，返回 {name: {name, description, content}}
load_skill(name)                  → 工具函数，返回指定技能的完整文档内容
```

**工作流程：**
1. `main()` 中调用 `scan_skills()` 获取所有技能
2. 技能名称和描述注入系统提示词，让 Agent 知晓可用技能
3. 注册 `load_skill` 工具，Agent 需要时加载技能全文
4. 新技能只需在 `skills/` 下新增子目录 + `skill.md`，无需改代码

**skill.md 格式：**
```yaml
name: skill-name
description: 技能描述
---
# 技能正文（Markdown）
```

---

### ⑤ HookSystem + 内置 Hooks

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

**内置 Hooks：**

| Hook | 阶段 | 职责 |
|---|---|---|
| `SecurityHook` | `pre_tool_use` | 三层安全审查 |
| `print_tool_use` | `post_tool_use` | 终端打印工具调用详情 |
| `todo_reminder_reset` | `pre_tool_use` | todo_write 调用时重置提醒计数 |
| `todo_reminder_check` | `prompt_submit` | 连续 3 轮未更新 todo 则注入提醒 |

#### SecurityHook — 安全审查

```
SecurityHook
├── __call__(tool_name, tool_args)  → Hook 入口
├── 第 1 层：硬拒绝                   → 匹配 hard_blocked_patterns 则抛 SecurityBlocked
├── 第 2 层：外部路径检查              → 检查命令是否操作项目目录之外
└── 第 3 层：用户确认                  → 询问用户 y/n
```

---

### ⑥ AgentLoop + SubAgent

#### AgentLoop

**职责：** 核心循环——反复调用 LLM → 解析响应 → 执行工具 → 继续循环，直到 LLM 返回纯文本。

```
AgentLoop
├── __init__(client, tool_registry, hook_system)
└── run(messages, max_turns=200) → 返回更新后的 messages
    流程：
      1. trigger(HOOK_PROMPT_SUBMIT)    ← todo 提醒注入
      2. LLM.chat_completion(messages, tools)
      3. 解析响应，检查 tool_calls
      4. 如果有工具调用：
         a. trigger(HOOK_PRE_TOOL_USE)  ← 安全审查、todo 重置
         b. tools.execute(name, args)   ← SecurityBlocked 在此捕获
         c. trigger(HOOK_POST_TOOL_USE) ← 工具打印
         d. 追加 tool 结果到 messages
         e. 回到步骤 1
      5. 无工具调用：
         a. trigger(HOOK_STOP_LOOP)
         b. 返回 messages
```

**关键设计：**
- **不管理记忆**——messages 由外部传入，loop 只消费和追加
- **不感知安全审查**——通过 Hook 接入，loop 只加了一层 `try/except SecurityBlocked`

#### run_subagent — 子 Agent

子 Agent 复用 `AgentLoop`，作为母 Agent 的工具调用。

**与母 Agent 的差异：**
- 工具集不含 `subagent` 自身（防递归调用），含 bash、todo_write
- 最大轮数限制为 30 次
- 系统提示词为独立于母 Agent 的子任务指令

#### 上下文压缩系统 — 五层压缩

**职责：** 防止 messages 列表无限增长，在 `main()` 中 `agent.run()` 前自动压缩。

```
五层压缩：
  第 1 层 compact_trim_messages    → 消息数量 > 50 时，保留前 3 + 后 47
  第 2 层 compact_tool_results     → 保留最近 3 轮工具结果，更早的用占位符替换
  第 3 层 compact_truncate_large   → 最后一条 user 消息 > 200KB 时，存档 > 30KB 的到 .transcripts/
  第 4 层 compact_summarize        → 总消息 > 80000 字符时，LLM 生成摘要
  第 5 层 compact_emergency        → API 返回 413 时，紧急裁切为最后 5 条
```

**触发链路：**
```
main() 每次循环:
  1~3 层自动执行 → 检查阈值 → 超限则第 4 层 → agent.run()
  → 如果 API 返回 413 → 第 5 层 → 重试 agent.run()

Agent 也可通过 compact 工具主动触发第 4 层
```

**关键设计：**
- **透明压缩**：前 3 层每次 `agent.run()` 前自动执行，Agent 无感知
- **LLM 摘要**：第 4 层调用 LLM 生成结构化摘要（保留目标、发现、文件、剩余工作、约束）
- **完整存档**：压缩前完整对话保存到 `.transcripts/`，随时可恢复
- **优雅降级**：第 5 层不依赖 LLM，纯文本拼接确保 413 后可恢复

---

### ⑦ main()

**职责：** 主交互入口，组装所有模块，管理对话记忆，驱动用户交互。

```
main()
├── 读取环境变量，初始化 LLMClient
├── 扫描技能（scan_skills），注册 load_skill 工具
├── 创建 ToolRegistry（create_default_tools）
├── 注册 subagent 工具
├── 创建 HookSystem + 注册所有内置 hooks
├── 创建 AgentLoop
├── 构建系统提示词（含技能列表）
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
│          │    创建       ┌───────────────────────┐
│          │──────────────→│  ToolRegistry           │
│          │              │  ├─ bash_execute        │
│          │              │  ├─ todo_write          │
│          │              │  ├─ run_subagent        │
│          │              │  └─ load_skill          │
│          │              └───────────────────────┘
│          │    扫描       ┌──────────────────┐
│          │──────────────→│  Skill 系统        │
│          │              │  ├─ scan_skills()  │
│          │              │  └─ load_skill()   │
│          │              └──────────────────┘
│          │    创建       ┌──────────────────────┐
│          │──────────────→│  HookSystem            │
│          │              │  ├─ SecurityHook       │
│          │              │  ├─ print_tool_use     │
│          │              │  └─ todo_reminder      │
│          │              └──────────────────────┘
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
    │                         system prompt 含技能列表
    ▼
LLMClient.chat_completion(messages, tools)
    │
    ▼
返回 response (含 tool_calls 或纯文本)
    │
    ├── 有 tool_calls → trigger(pre_tool_use)    ← 安全审查 + todo 提醒
    │                       │
    │                   ToolRegistry.execute(name, args)
    │                       │
    │                       ├── bash_execute     → shell 执行
    │                       ├── todo_write       → 更新任务看板
    │                       ├── run_subagent     → 启动子 Agent
    │                       └── load_skill       → 返回技能文档
    │                       │
    │                       ▼
    │                   trigger(post_tool_use)   ← 打印工具详情
    │                       │
    │                       ▼
    │                   tool 结果 → 追加到 messages → 继续循环
    │
    └── 无 tool_calls → trigger(stop_loop) → messages 返回给 main()
                            │
                            ▼
                        main() 打印最后一条 content
```

---

## 约定与原则

1. **记忆在 main 中管理**——AgentLoop 只消费和追加，不初始化、不清空
2. **扩展通过 Hook**——新增功能优先写 Hook 函数，注册到对应阶段，不改 Loop
3. **工具只需注册**——写工具函数 + 注册即可，AgentLoop 自动感知
4. **安全是横切关注点**——SecurityHook 通过 Hook 接入，与核心逻辑解耦
5. **技能即文档**——Skill 是存储在文件系统中的 Markdown 文档，通过 `load_skill` 工具按需加载
6. **子 Agent 复用循环**——SubAgent 复用 `AgentLoop` 类，仅限制工具集和轮数
