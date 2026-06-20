# 工具（Tool）创建规范

## 概述

本框架的工具系统基于 OpenAI Function Calling 规范。一个工具 = **一个 Python 函数 + 一个 JSON schema 描述**，注册到 `ToolRegistry` 后，LLM 可以在对话中自动调用它。

---

## 整体结构

```
编程实现
  └─ ToolRegistry              ← 工具注册表（管理所有工具）
       └─ Tool                 ← 工具包装器
            ├─ name            ← 工具名（LLM 用来识别）
            ├─ description     ← 描述（LLM 判断何时用）
            ├─ parameters      ← 参数 schema（JSON Schema 格式）
            └─ fn              ← 实际执行的 Python 函数
```

### 核心类说明

**`Tool`**（`coding-agent.py` 第 67-94 行）
- 接收一个普通 Python 函数，包装成 LLM 可调用的格式
- `to_openai_tool()` 生成标准的 OpenAI function calling JSON

**`ToolRegistry`**（`coding-agent.py` 第 97-116 行）
- 用字典存储所有 Tool 对象
- `register(tool)` — 注册
- `execute(name, args)` — 按名执行

---

## 三步创建一个新工具

### 第 1 步：写工具函数

写一个普通的 Python 函数，接收参数，返回字符串。

```python
def read_file(path: str, encoding: str = "utf-8") -> str:
    """读取本地文件内容"""
    try:
        with open(path, "r", encoding=encoding) as f:
            return f.read()
    except Exception as e:
        return f"Error: {e}"
```

**规范：**
- 返回值**必须是 `str`**（会被 `Tool.execute()` 自动转换）
- 参数名应简洁、语义清晰
- 函数应有 docstring，说明功能

### 第 2 步：编写 parameters JSON Schema

parameters 是 JSON Schema 格式，描述函数的参数。LLM 根据这个 schema 生成参数。

```python
parameters = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "文件的完整路径",
        },
        "encoding": {
            "type": "string",
            "description": "文件编码，默认 utf-8",
            "default": "utf-8",
        },
    },
    "required": ["path"],
}
```

**规范：**
- `type` 固定为 `"object"`
- `properties` 中每个参数标注 `type` 和 `description`
- `description` 准确描述参数的用途（影响 LLM 生成参数的质量）
- `required` 列出必填参数
- 有默认值的参数**不**放在 `required` 中，在 `properties` 中用 `default` 标注

> 支持的 `type`：`string`, `integer`, `number`, `boolean`, `array`, `object`

### 第 3 步：注册到 `create_default_tools()`

在 `coding-agent.py` 的 `create_default_tools()` 函数中调用 `registry.register()`：

```python
def create_default_tools() -> ToolRegistry:
    registry = ToolRegistry()

    # 已有工具
    registry.register(Tool(
        name="bash",
        description="执行 shell 命令...",
        parameters={...},
        fn=bash_execute,
    ))

    # 新增工具
    registry.register(Tool(
        name="read_file",
        description="读取本地文件内容",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件的完整路径",
                },
                "encoding": {
                    "type": "string",
                    "description": "文件编码，默认 utf-8",
                    "default": "utf-8",
                },
            },
            "required": ["path"],
        },
        fn=read_file,
    ))

    return registry
```

---

## 完整示例

```python
# 1. 工具函数
def list_dir(path: str = ".") -> str:
    """列出目录内容"""
    import os
    try:
        items = os.listdir(path)
        return "\n".join(items) if items else "(空目录)"
    except Exception as e:
        return f"Error: {e}"

# 2. 注册（在 create_default_tools 中）
registry.register(Tool(
    name="list_dir",
    description="列出指定目录下的文件和文件夹",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要列出的目录路径，默认为当前目录",
                "default": ".",
            },
        },
    },
    fn=list_dir,
))
```

---

## 注意事项

1. **函数返回值必须是字符串**，如果要返回结构化数据可用 `json.dumps()`
2. **`name` 不能重复**，注册相同 name 会覆盖前面的
3. **`description` 要写详细**，LLM 根据这个判断什么时候调用该工具
4. **不要在工具函数里直接修改 Agent 状态**，工具只负责返回结果
5. **函数应该健壮**，内部 try/except 自行处理异常，不要向外抛出
6. **AgentLoop 不需要改**，注册后自动生效，零耦合
