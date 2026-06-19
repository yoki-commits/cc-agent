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

