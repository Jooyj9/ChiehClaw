# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在本仓库中工作时提供指导。

## 项目概览

xxxclaw 是一个最小化的 Python 编程助手，借鉴 `pi-mono` 架构。它提供 LLM 驱动的工具调用能力（文件操作、shell 命令）、会话持久化，以及可选的飞书 IM 接入。

## 构建与运行

```bash
uv sync                          # 安装依赖
uv run python -m app.main        # 交互式 CLI 模式
uv run python -m app.main --prompt "ls ."   # 单轮对话
uv run python -m app.main --no-stream --show-tool-status --prompt "read README.md"
uv run python -m app.adapters.feishu.long_connection   # 飞书 IM 机器人
uv run python -m unittest        # 运行所有测试
```

未配置 `OPENAI_API_KEY` 时默认使用 `mock` 模式——它会针对 `ls`、`read <路径>`、`shell <命令>` 返回模拟的工具调用，无需 API 即可在本地跑通主链路。

## 架构：四层模型

代码库按 `pi-mono` 的分层思想组织，自底向上：

### `app/ai` — 统一 LLM 接口

- `message.py`：规范的消息类型——`ChatMessage`（角色：用户/助手/工具）、`AssistantMessage`、`ToolCall`。均提供 `to_dict()`/`from_dict()` 用于 JSON 持久化，以及 `to_openai_dict()` 用于 API 调用。
- `base.py`：抽象类 `LLMClient`，定义 `generate()` 和 `stream()` 方法。两者均接收 `messages`、可选的 `tools` 和可选的 `system_prompt`。
- `client_openai.py`：`OpenAICompatibleClient`——直接使用 `urllib`（不依赖第三方 SDK）。支持非流式和 SSE 流式，含指数退避重试。
- `client_mock.py`：`MockLLMClient`——基于关键词触发工具（`ls`、`read`、`shell` 前缀）。未配置 API 密钥时使用。
- `models.py`：`ModelConfig` 数据类（provider、model name、temperature）。

### `app/agent` — 工具调用循环

- `tools.py`：`Tool` 数据类，包含 `name`、`skill_name`、`description`、`parameters_schema`、`handler`、`idempotent`/`recoverable` 标记，以及 `to_openai_schema()`。五个默认工具：`read_file`、`write_file`、`edit_file`、`ls`、`run_shell`（支持本地子进程和 Docker 沙箱两种模式）。
- `tool_executor.py`：`ToolExecutor` 将 `ToolCall` 分派给对应的 handler，返回 `{"ok": True/False, ...}`。
- `loop.py`：`AgentLoop.run()`——同步工具循环：调 LLM → 如有 tool_calls 则逐个执行 → 追加结果 → 重复（最多 6 轮）。通过事件接收器发出结构化事件。
- `session.py`：`Session` 数据类（conversation_id、system_prompt、messages 列表、metadata 字典）。通过 `to_dict()`/`from_dict()` 读写 JSON。
- `skills.py`：`SkillRouter` 根据用户输入关键词和最近消息历史激活工具集。两个技能：`workspace`（文件工具）和 `shell`（命令执行）。无命中时默认启用 `workspace`。

### `app/runtime` — 编排层

- `runner.py`：`Runner` 是主入口。`Runner.from_env()` 读取全部配置，构建 LLM 客户端、工具、技能路由、上下文管理器、会话存储、事件日志和恢复管理器。`run_turn(conversation_id, user_input)` 是核心 API——加载会话 → 通过技能选择工具 → 构建系统提示 → 运行 agent loop → 保存会话 → 返回 `TurnResult`。
- `context_manager.py`：`ContextManager.build_messages()` 为每次 LLM 调用组装消息列表。超出 token 预算时，将消息拆分为最近窗口 + 旧记忆（基于关键词相关性打分）。同时注入运行时上下文消息（工作区路径、shell 模式）、项目记忆（来自 `XAGENTS.md`）和固定上下文（初始目标、关键文件）。会截断过长的工具结果和推理内容。
- `prompt_builder.py`：根据当前激活的技能和工具构建系统提示。
- `workspace_context.py`：通过 `ContextVar` 跟踪每个会话的当前工作区目录。`use_workspace_dir()` 是上下文管理器；工具调用 `get_workspace_dir()` 解析路径。
- `recovery.py`：`RecoveryManager` 扫描事件日志，找出已启动但未达到终态（`turn_succeeded`/`turn_failed`）的回合。启动时 `Runner.recover_unfinished_tasks()` 自动重放幂等工具（read_file、ls），非幂等工具则留待用户确认。
- `project_memory.py`：加载项目级记忆文件（默认 `XAGENTS.md`），作为系统消息注入。
- `event_log.py`：结构化 JSONL 事件日志，用于可观测性和崩溃恢复。
- `token_counter.py`：上下文预算管理的 token 估算。

### `app/adapters` — 外部协议适配

- `base.py`：`IncomingMessage` 数据类——归一化的入站消息格式。
- `feishu/`：
  - `long_connection.py`：飞书主入口。使用 `lark-oapi` WebSocket 客户端接收 IM 消息，经 runner 处理后通过飞书 API 回复。每个会话有独立的线程锁保证串行执行。
  - `webhook.py`：HTTP webhook 处理器（长连接的替代方案）。
  - `router.py`：将飞书事件负载映射为 `IncomingMessage`，支持群聊会话作用域（按用户、按聊天、按线程）。
  - `formatter.py`：将 runner 输出格式化为飞书消息卡片。
  - `client.py`：`FeishuAPIClient`——REST API 调用（回复、发送、获取资源）。

### `app/storage` — 持久化

- `session_store.py`：`JsonSessionStore`——每个会话一个 JSON 文件（`data/sessions/{id}.json`）。
- `file_store.py`：工作区文件管理。
- `naming.py`：将会话 ID 转为文件系统安全的名称。

### `app/utils` — 共享工具

- `logger.py`：`append_jsonl()` 结构化日志写入。
- `paths.py`：`resolve_workspace_path()`——在工作区内解析相对路径，阻止路径穿越。
- `env.py`：加载 `.env` 文件。
- `json_io.py`：原子化的 JSON 读写辅助。

## 配置

所有配置来自环境变量（自动加载 `.env`），详见 `app/config.py` 中的 `Settings.from_env()`。关键变量：

- `XXXCLAW_MODEL_PROVIDER`：`mock`（无 API 密钥时的默认值）或 `openai`
- `OPENAI_API_KEY` / `MOONSHOT_API_KEY`：API 密钥（默认指向 Moonshot API）
- `OPENAI_BASE_URL`：默认为 `https://api.moonshot.cn/v1/chat/completions`
- `XXXCLAW_WORKSPACE_ROOT_DIR`：文件/shell 工具的工作目录
- `XXXCLAW_SHELL_EXECUTION_MODE`：`local` 或 `docker`
- `XXXCLAW_CONTEXT_MAX_TOKENS`、`XXXCLAW_CONTEXT_RECENT_TOKENS`、`XXXCLAW_CONTEXT_OLDER_MEMORY_TOKENS`：上下文窗口管理
- 功能开关：`XXXCLAW_FEATURE_RUNTIME_CONTEXT`、`XXXCLAW_FEATURE_OLDER_MEMORY`、`XXXCLAW_FEATURE_RECOVERY`、`XXXCLAW_FEATURE_WORKSPACE_ISOLATION`

## 关键设计约定

- **LLM 调用不使用第三方 SDK**：`client_openai.py` 直接用 `urllib`，不依赖 `openai` 包。
- **幂等工具可自动恢复**：标记为 `idempotent=True` 且 `recoverable=True` 的工具（`read_file`、`ls`）崩溃后可自动重放。`recoverable=False` 的工具则通知用户确认。
- **工作区隔离**：`XXXCLAW_FEATURE_WORKSPACE_ISOLATION=true` 时，每个会话在工作区根目录下拥有独立子目录。`resolve_workspace_path()` 会阻止穿越工作区的路径访问。
- **按技能筛选工具**：`SkillRouter` 通过关键词匹配只激活当前相关的工具子集，而非每轮发送全部工具，减少 token 消耗。
- **基于事件溯源的恢复**：每个回合和工具调用都以结构化 JSONL 事件记录。恢复时扫描有 `tool_call_started` 但无终态事件的回合进行重放。
- **系统提示按轮构建**：系统提示每轮根据当前激活的技能和工具重新生成，不会静态持久化。
