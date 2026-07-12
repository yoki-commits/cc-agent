# cc-agent — Agent 搭建学习项目

## 项目定位

这是一个**从零搭建 Coding Agent 的学习项目**。通过逐步迭代，理解 Agent 的核心架构：LLM 客户端、工具系统、Hook 机制、安全审查等模块。

项目以 "Phase" 为单位演进，每个 Phase 在现有代码上新增一个功能模块，逐步构建一个功能完整的 Agent。

## 当前项目结构

```
learn-deepseek-code/
├── coding-agent.py   # 主程序：Agent 的全部代码
├── ARCHITECTURE.md   # 架构说明 — 模块职责、调用关系、数据流
├── CLAUDE.md         # 本文件 — 项目说明与 AI 协作约定
├── hook-spec.md      # Hook 系统设计文档
├── tool-spec.md      # Tool 系统设计文档
├── README.md         # 各 Phase 变更记录
├── skills/           # 技能文件目录
│   ├── code-review/
│   ├── debugger/
│   ├── agent-builder/
│   ├── mcp-builder/
│   └── pdf/
├── .gitignore
└── LICENSE
```

## 学习方式

每次迭代的核心模式：

1. **提出需求** — 描述要新增或修改的功能
2. **理解现状** — 阅读当前代码，理解各模块如何协作
3. **最小改动** — 只修改必要的模块，保持低耦合
4. **验证可用** — 确保修改后 Agent 能正常运行

## 协作约定

- **渐进式修改**：每次只改动最小模块，不改无关代码
- **保持低耦合**：尽量使每个模块保持低耦合性，修改模块时不会相互影响。
- **记录变更**：README.md 记录每个 Phase 做了什么、原提示词是什么
- **提问前置**：如果需求有歧义，先澄清再动手
- **新增工具遵循 tool-spec**：增加新工具时按 `tool-spec.md` 规范执行，不确定时先读取该文件
- **新增 Hook 遵循 hook-spec**：增加新 Hook 时按 `hook-spec.md` 规范执行，不确定时先读取该文件
- **同步更新 ARCHITECTURE.md**：新增模块或对已有模块做结构性改动时（不包括修小 bug 或极小改动），**必须**同步更新 `ARCHITECTURE.md` 中的模块说明、调用关系、数据流、工具列表、内置 Hooks 表格等内容。
- **同步更新 CLAUDE.md**：每次修改项目的目录结构（新增/删除/移动文件或目录），**必须**同步修改 `CLAUDE.md` 中「当前项目结构」部分的目录树。

