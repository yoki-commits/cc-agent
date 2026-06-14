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

