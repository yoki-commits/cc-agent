# cc-agent — 从零搭建 Coding Agent

记录从零搭建一个基础 Agent 的全过程，按阶段划分。

---

## Phase 1 — 基础框架搭建

**提示词：**

> 搭建一个最基础的 agent，新建一个客户端，创建一个工具列表，定义工具 bash，实现工具函数。新建一个 agentloop 循环，再建立主交互入口，主交互入口中实现记忆功能，不是在 agentloop 中实现。

**实现内容：**
- `LLMClient` — 封装 DeepSeek API 调用（OpenAI 兼容格式）
- `Tool` / `ToolRegistry` — 工具注册与执行系统
- `bash` 工具 — 通过 `subprocess.run` 执行 shell 命令
- `AgentLoop` — 循环调用 LLM + 工具，直到返回纯文本回复
- `main()` — 主交互入口，管理对话记忆（`messages` 列表）

**提交：** `s01agent实现`

---

## Phase 2 — 工具扩展方式说明

**提示词：**

> 以后我再增加新的工具怎么做

**回答要点：** 两步即可：① 写工具函数 → ② 在 `create_default_tools()` 中注册 `Tool(...)`，无需修改 `AgentLoop`。

---

## Phase 3 — Hook 触发机制

**提示词：**

> 我要给这个 agent 加上 hook 触发机制，要包含三个部分，
> 1. 一个是字典列表，标注四个阶段 prompt_submit, pre_tool_use, 结束工具使用，停止循环，有这四个 hook，
> 2. 建立新增 hook 的功能函数，输入对应的阶段和调用的工具名称
> 3. 新建触发 hook 的函数，输入调用哪个阶段 hook 的名称
> 4. hook 的具体实现函数先空置，之后再加
> 要保证对 agent 的循环改动最小，循环只负责调用

**实现内容：**
- `HookSystem` 类 — 字典列表存储各阶段 hook，支持 `add_hook(phase, fn)` 注册和 `trigger(phase, **kwargs)` 触发
- 四个阶段：`prompt_submit` / `pre_tool_use` / `post_tool_use` / `stop_loop`
- `AgentLoop` 仅插入 4 行 `self.hooks.trigger(...)`，核心逻辑零改动
- `AgentLoop.__init__` 新增可选 `hook_system` 参数，不传则自动创建空 HookSystem

**提交：** `add hook system`

---

## Phase 4 — 安全审查钩子（三层检查）

**提示词：**

> 我要实现第一个具体钩子函数，把安全审查机制迁移到 pre_tool_use 的 hook 中，agent 循环只负责一句调用，安全系统的设计也独立开，仅把这个功能用一个 add_hook 函数添加到 hook 中，保证各部分低耦合性
>
> 安全审查机制：分为三层，第一层硬拒绝层，风险大，如删除系统，删除根目录，关机，重启等命令，用列表列出，要调用工具执行时，直接拒绝；第二层，轻风险命令，要在当前项目目录外操作等情况，也用列表列出，在命中后进入第三层，询问用户是否同意，用户拒绝则拒绝执行

**实现内容：**
- `SecurityBlocked` 异常 — hook 通过抛出此异常阻止工具执行
- `SecurityHook` 类 — 三层安全审查：
  - **第 1 层** — 硬拒绝：`hard_blocked_patterns`（rm -rf /、shutdown、format、userdel 等），直接拦截
  - **第 2 层** — 检查命令是否操作项目目录外的路径
  - **第 3 层** — 用户终端确认：`y` 放行，`n`/回车拒绝
- `AgentLoop` 仅加 `try/except SecurityBlocked` 包围 `tools.execute()`，其余零改动
- `main()` 中通过 `hooks.add_hook(HOOK_PRE_TOOL_USE, security)` 注册

**提交：** `add security hook - 三层安全审查`

---

## Phase 5 — 任务规划系统

**提示词：**

> 1. 我要新增一个工具 todo_write，让 AI 规划这个任务的步骤为一个字典列表，列表包含任务、状态（等待执行/正在执行/已完成），并且每次更新之后打印出来。
> 2. 增加提醒机制，在每隔 3 轮没有更新 todo_list 时，下一轮对话注入一句简短的提示词提醒

**实现内容：**
- `todo_write` 工具 — 接收 JSON 格式任务数组，存储到模块级状态 `_todo_tasks`，终端打印格式化看板
- **提醒机制**（基于 Hook）：
  - `todo_reminder_reset` — 注册到 `pre_tool_use`，调用 `todo_write` 时重置计数器
  - `todo_reminder_check` — 注册到 `prompt_submit`，连续 3 轮未更新则注入一条 user 提醒消息
- 系统提示词同步更新，告知 AI 可使用 `todo_write`
- 所有代码零耦合：工具注册到 `create_default_tools()`，提醒逻辑通过 `add_hook()` 接入

**提交：** `新增 todo_write 工具 + 任务更新提醒机制`

---

## Phase 6 — 子 Agent 系统

**提示词：**

> 接下来这一部分是加如子Agent的功能。需要完成：
> 首先把子Agent的这个功能作为一个工具加入到Agent的工具目录里面，就是调用子Agent完成任务，工具的实现和原来的Agent的循环差不多，
> 有三个区别和原agent，子Agent的工具里面没有调用此Agent的工具。
> 限制子Agent的循环次数30次，系统提示词和母agent不一样

**实现内容：**
- `run_subagent(task)` 函数 — 作为 `subagent` 工具的实现
- 子 Agent 复用 `AgentLoop` 类，与母 Agent 共享循环逻辑
- **与母 Agent 的差异**：
  - 工具集不含 `subagent` 自身（防递归），仅含 `bash` 和 `todo_write`
  - 最大轮数限制为 30 次
  - 独立于母 Agent 的子任务系统提示词
- 注册在 `main()` 中（而非 `create_default_tools()`），避免子 Agent 能调用子 Agent

**提交：** `新增 subagent 子 Agent 工具 + 更新 README`

---

## Phase 7 — Skill 系统

**提示词：**

> 现在我们来做这个Skill系统，所有的skill放在skills这个目录下面，目录下面每一个子目录都是一个skill，子目录里面的skill.md是技能介绍
> 1. 要有一个扫描skill的函数，把所有skill都扫描出来，从skill.md里面，YAML部分有两个字段，一个是name，另一个是description，整个全文是content，把这些内容都读取出来，建立一个字典，key是技能的名字，value里面是包含三个字段的字典
> 2. 把读取出的name和description列表注入系统提示词，让Agent知道有这个技能
> 3. 创建一个工具load_skill，当Agent要使用某个对应技能的时候，加载全文

**实现内容：**
- `skills/` 目录 — 每个子目录一个技能，含 `skill.md`（YAML 头 + Markdown 正文）
- `scan_skills()` 函数 — 扫描 `skills/` 目录，解析 YAML 头，返回 `{name: {name, description, content}}`
- `load_skill(name)` 工具 — 返回指定技能的完整文档内容
- 系统提示词注入技能名称和描述列表
- **示例技能**：`code-review`、`debugger`、`agent-builder`、`mcp-builder`、`pdf`
- 新增技能只需在 `skills/` 下新增子目录，无需改代码

**提交：** `新增 Skill 系统 + 更新架构文档 + 协作约定`

---

## Phase 8 — 上下文压缩系统

**提示词：**

> 防止上下文过多，完成对上下文压缩功能的新增，功能分为四层（实际为五层）：
> 1. 消息裁切，保留最新 47 条 + 最前 3 条
> 2. 工具结果压缩，保留最近 3 轮
> 3. 超大消息截断存档（200KB / 30KB）
> 4. LLM 摘要压缩（80000 字符阈值）
> 5. 紧急裁切（413 响应时）
> 前三种在 main 中自动执行，第四种按阈值触发，第五种 API 413 时触发。另加 compact 工具让 Agent 主动调用。

**实现内容：**
- **五层压缩函数**：
  - `compact_trim_messages` — 保留前 3 + 后 47 条，中间省略
  - `compact_tool_results` — 保留最近 3 轮工具结果，更早替换为占位符
  - `compact_truncate_large` — 单条 > 200KB 时存档到 `.transcripts/`，留 2000 字符预览
  - `compact_summarize` — 总消息 > 80000 字符时保存完整对话，LLM 生成结构化摘要
  - `compact_emergency` — 仅保留 system prompt + 简单摘要 + 最后 5 条
- **`compact` 工具** — Agent 可主动触发第 4 层 LLM 摘要
- **413 自动恢复** — API 返回 prompt_too_long 时自动紧急裁切并重试
- 完整对话存档到 `.transcripts/`，随时可恢复

**提交：** `上下文压缩系统`

