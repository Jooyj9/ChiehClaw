# Agent Platform Stability and Context Guide

目标：在尽量少改现有架构的前提下，把 xxxclaw 从“可运行的单 Agent”推进到“更接近平台服务”的形态。

本 guide 只作为后续编码依据，不要求一次性全部实现。优先顺序是：

1. 先补 event log，提升崩溃恢复粒度。
2. 再把上下文管理从“按消息条数”升级为“滑动窗口 + token budget”。
3. 最后再考虑 retrieval-driven context。

## 现状

当前主链路：

```text
Runner.run_turn(...)
-> session_store.load(conversation_id)
-> skill_router.select_tools(...)
-> session.add_user_message(...)
-> AgentLoop.run(...)
   -> llm_client.generate / stream
   -> session.add_assistant_message(...)
   -> tool_executor.execute(...)
   -> session.add_tool_result(...)
-> session_store.save(session)
```

当前优点：

- CLI / 飞书都复用 `Runner`，入口已经解耦。
- `conversation_id` 已经能恢复会话历史。
- `AgentLoop` 有 `max_tool_rounds`，能避免无限 tool loop。
- `ContextManager` 已经有 summary、最近窗口和大 tool result 截断。

当前不足：

- session 是“单轮结束后保存”为主，崩溃时只能恢复到最近一次已保存状态。
- tool call 没有独立状态，无法区分 `planned` / `running` / `succeeded` / `failed`。
- tool result 没有 append-only 持久化，进程中断后不方便判断是否已执行过。
- 上下文按消息条数裁剪，不感知 token；一条很长的工具输出可能比多条普通对话更贵。

## 设计原则

- 不重写现有 `Runner` / `AgentLoop` / `SessionStore`。
- 新增持久化能力时优先使用 JSONL，符合当前 `append_jsonl` 风格。
- 先做单机可靠性，不直接引入数据库、任务队列或分布式锁。
- 恢复机制先支持“识别未完成任务并给出安全处理”，再逐步支持自动恢复。
- tool 默认按非幂等处理，只有明确标记幂等的 tool 才允许自动重放。

## Part 1: Event Log and Recovery

### 目标

把恢复粒度从“会话级”细化到“turn / llm_call / tool_call 级”。

期望达到：

- 服务重启后能扫描未完成 turn。
- 能知道某个 tool call 是尚未执行、执行中断、执行成功还是执行失败。
- tool 执行结果以 append-only 方式保存，减少结果丢失和覆盖风险。
- 对危险或非幂等 tool 不自动重放，只把状态反馈给用户或后续恢复逻辑。

### 最小新增文件

建议新增：

```text
app/runtime/event_log.py
app/runtime/recovery.py
```

数据目录：

```text
data/events/{conversation_storage_name}.jsonl
```

`event_log.py` 只负责追加事件和读取事件：

```python
class EventLog:
    def append(self, conversation_id: str, event: dict) -> None: ...
    def read(self, conversation_id: str) -> list[dict]: ...
    def iter_conversations(self) -> Iterator[str]: ...
```

`recovery.py` 负责扫描事件并判断 unfinished turn：

```python
class RecoveryManager:
    def scan_unfinished(self) -> list[UnfinishedTurn]: ...
    def rebuild_state(self, conversation_id: str) -> RecoveryState: ...
```

### Event Schema

每行一个 JSON event，必须 append-only。

通用字段：

```json
{
  "event_id": "uuid",
  "conversation_id": "local-cli",
  "turn_id": "uuid",
  "ts": "2026-05-28T12:00:00Z",
  "type": "turn_started"
}
```

建议事件类型：

```text
turn_started
user_message_appended
llm_call_started
llm_call_succeeded
llm_call_failed
assistant_message_appended
tool_call_planned
tool_call_started
tool_call_succeeded
tool_call_failed
tool_result_appended
turn_succeeded
turn_failed
turn_recovered
```

tool 相关事件示例：

```json
{
  "event_id": "uuid",
  "conversation_id": "feishu-group-cxxx-user-uxxx",
  "turn_id": "uuid",
  "tool_call_id": "call_1",
  "type": "tool_call_started",
  "tool_name": "read_file",
  "arguments": {
    "path": "README.md"
  },
  "ts": "2026-05-28T12:00:01Z"
}
```

成功结果：

```json
{
  "event_id": "uuid",
  "conversation_id": "feishu-group-cxxx-user-uxxx",
  "turn_id": "uuid",
  "tool_call_id": "call_1",
  "type": "tool_call_succeeded",
  "tool_name": "read_file",
  "result": {
    "ok": true,
    "tool": "read_file",
    "result": {
      "path": "D:/agentzero/xxxclaw/README.md",
      "content": "..."
    }
  },
  "ts": "2026-05-28T12:00:02Z"
}
```

### Tool 状态机

每个 tool call 使用下面的状态：

```text
planned -> started -> succeeded
planned -> started -> failed
planned -> skipped
started -> interrupted
```

含义：

- `planned`：LLM 已经返回 tool call，但还没有执行。
- `started`：服务端准备执行 tool，已经写入 WAL。
- `succeeded`：tool 执行完成，结果已经 append-only 持久化。
- `failed`：tool 执行抛异常或返回错误。
- `interrupted`：重启扫描时发现有 `started`，但没有 `succeeded` / `failed`。
- `skipped`：恢复时判断该 tool 不适合自动重放。

### 最小代码改动点

#### 1. Runner 增加 turn_id 和事件日志

在 `Runner._run_turn_internal` 开始时生成 `turn_id`，并写：

```text
turn_started
user_message_appended
```

在 finally 中根据是否异常写：

```text
turn_succeeded / turn_failed
```

仍然保留原来的 `session_store.save(session)`。

#### 2. AgentLoop 在 LLM 和 tool 前后写事件

最小侵入方式：

- 给 `AgentLoop.run(...)` 增加可选参数 `event_sink=None`。
- 不传时保持现有行为。
- `Runner` 传入 event sink。

事件写入位置：

```text
LLM 调用前: llm_call_started
LLM 成功后: llm_call_succeeded
LLM 失败后: llm_call_failed
assistant 入 session 后: assistant_message_appended
tool 执行前: tool_call_planned + tool_call_started
tool 成功后: tool_call_succeeded + tool_result_appended
tool 失败后: tool_call_failed + tool_result_appended
```

#### 3. ToolExecutor 先不改接口

为了少改代码，`ToolExecutor.execute(tool_call)` 可以暂时不动。

event log 由 `AgentLoop` 包在调用前后写入：

```text
event_log.append(tool_call_started)
result = tool_executor.execute(tool_call)
event_log.append(tool_call_succeeded or tool_call_failed)
session.add_tool_result(...)
event_log.append(tool_result_appended)
```

后续如果要做更强的幂等和权限，再扩展 `Tool` 元数据。

### Tool 幂等策略

建议给 `Tool` 增加可选字段：

```python
idempotent: bool = False
recoverable: bool = False
```

初始建议：

```text
read_file: idempotent=True, recoverable=True
ls: idempotent=True, recoverable=True
write_file: idempotent=False, recoverable=False
edit_file: idempotent=False, recoverable=False
run_shell: idempotent=False, recoverable=False
```

恢复时：

- `read_file` / `ls` 可以自动重放。
- `write_file` / `edit_file` / `run_shell` 默认不重放。
- 不重放时向 session 追加一条 tool result 或 assistant message，说明任务中断，需要用户确认。

### 重启恢复流程

应用启动时可以调用：

```text
RecoveryManager.scan_unfinished()
```

恢复规则：

1. 如果 turn 已有 `turn_succeeded`，忽略。
2. 如果 turn 有 `turn_failed`，只记录，不自动继续。
3. 如果存在 `tool_call_started` 但没有终态事件，标记为 `interrupted`。
4. 如果 interrupted tool 是 recoverable，可重新执行并补写事件。
5. 如果 interrupted tool 不 recoverable，追加一条恢复说明到 session。

恢复说明示例：

```text
Previous turn was interrupted while running tool `run_shell`.
The command was not replayed automatically because it may have side effects.
Please confirm whether to continue.
```

### 为什么不用一开始就上 Redis / DB

当前项目是本地轻量 coding agent，JSON session 已经存在。先加 JSONL WAL 有几个好处：

- 改动小。
- 方便调试。
- 和现有日志方式一致。
- 后续可以平滑迁移到 SQLite / PostgreSQL / Redis Stream。

未来平台化后可迁移：

```text
JSONL EventLog -> SQLite EventLog -> PostgreSQL EventLog / Redis Stream
```

## Part 2: Context Management

### 现状问题

旧版 `ContextManager` 使用 `max_messages=24` 控制消息数量，容易出现“很多短消息挤掉早期关键长消息”的问题。

问题：

- 消息条数不等于 token 数。
- 大型 tool result 会显著挤占上下文。
- 最近消息不一定最重要，早期需求、文件路径、约束条件可能更重要。
- 窗口太小会让多轮 coding task 丢失任务连续性。

### 目标方案

采用三层上下文：

```text
Pinned Context
+ Token-based Sliding Window
+ Older Memory
```

#### 1. Pinned Context

永远优先保留：

- 首轮用户目标。
- 当前未完成任务。
- 最近一次失败原因。
- 关键文件路径。
- 用户明确约束，例如“不要改某文件”“只用 Python”。
- 注意：不要把 pinned context 做成第二份完整摘要，只保留稳定、短小、必须跨轮保留的信息。

这些内容放在 session metadata 中：

```json
{
  "pinned_context": {
    "initial_goal": "...",
    "key_files": ["app/runtime/runner.py"]
  }
}
```

#### 2. Token-based Sliding Window

主控制改为 token budget，不再暴露 `max_messages` 配置，避免继续形成“最近 N 条”的心智负担。

建议新增配置：

```text
XXXCLAW_CONTEXT_MAX_TOKENS=24000
XXXCLAW_CONTEXT_RECENT_TOKENS=16000
XXXCLAW_CONTEXT_OLDER_MEMORY_TOKENS=4000
```

如果当前模型上下文更大，可以把默认窗口调大一些，例如：

```text
context_max_tokens: 32000
recent_tokens: 22000
older_memory_tokens: 5000
```

轻量实现可以先不用真正 tokenizer，先用估算：

```python
estimated_tokens = max(1, len(text) // 4)
```

后续再接 `tiktoken` 或模型对应 tokenizer。

#### 3. Older Memory

先不要上向量数据库，避免架构变重。

第一阶段只在 `ContextManager` 内部从不在 recent window 的旧消息中筛选关键片段：

- 从旧的 tool result、用户消息、assistant tool call 中提取关键词。
- 按当前 user_input 与历史消息的关键词重叠打分。
- 结合错误、文件路径、tool 调用等重要性信号打分。
- 最终压成一条 `older_memory` synthetic message。

接口：

```text
Older memory 由 `ContextManager` 内部完成，不再单独引入 retriever 模块，避免上下文层过度拆分。
```

### 新 ContextManager 构造策略

目标输出仍然是：

```python
build_messages(session: Session) -> list[ChatMessage]
```

保持 `AgentLoop` 不变。

内部步骤：

```text
1. sanitize 所有消息
2. 构建 pinned context message
3. 从尾部开始按 token budget 收集 recent messages
4. 对未进入窗口的历史筛选关键信息，压成一条 older_memory
5. 合并为 [pinned, older_memory, recent]
6. 更新 session.metadata 中的预算统计
```

建议顺序：

```text
system prompt
-> pinned context message
-> older_memory
-> recent sliding window
```

### Token Budget 分配

默认建议：

```text
Pinned context: 10%
Older memory: 25%
Recent window: 60%
```

对于 coding agent，recent window 可以更大，因为最近 tool result 往往直接影响下一步。

例如 `max_context_tokens=32000`：

```text
pinned: 2000
older_memory: 5000
recent: 20000
```

### Older Memory 优化

当前 `older_memory` 是规则生成，不单独引入 summary message 和 retriever 模块。

后续增强：

- 规则筛选作为默认方案。
- 当历史很长时，可选调用 LLM 生成更高质量 older memory。
- 内容按结构组织，而不是普通自然语言。

建议 older memory 格式：

```text
Older memory:
- Key previous decision:
- Related file/error/tool result:
- Important previous note not present in recent window:
```

### Metadata 统计

建议在 `session.metadata` 中写入：

```json
{
  "context_stats": {
    "estimated_total_tokens": 18320,
    "pinned_tokens": 780,
    "older_memory_tokens": 3200,
    "recent_tokens": 12240,
    "trimmed_message_count": 18
  }
}
```

这样后续能做 benchmark 和面试展示：

- 压缩前 token 数。
- 压缩后 token 数。
- 保留 pinned / older memory / recent 的比例。
- 长对话任务成功率是否提升。

## 分阶段落地计划

### Phase 1: WAL and Tool Status

目标：不改变行为，只增加事件日志。

改动：

- 新增 `EventLog`。
- `Runner` 写 turn 事件。
- `AgentLoop` 写 LLM 和 tool 事件。
- tool 结果 append-only 保存。

验收：

- 正常对话后，`data/events/*.jsonl` 能看到完整 turn。
- tool 成功和失败都有状态事件。
- 原有测试通过。

### Phase 2: Basic Recovery Scan

目标：重启后能识别 unfinished turn。

改动：

- 新增 `RecoveryManager.scan_unfinished()`。
- 启动时扫描 event log。
- 对 interrupted tool 写日志或 session 恢复说明。

验收：

- 人工构造 `tool_call_started` 无终态事件，能被扫描出来。
- 非 recoverable tool 不自动重放。
- recoverable tool 可以选择自动重放。

### Phase 3: Token-based Context Window

目标：上下文从按条数控制改成按 token budget 控制。

改动：

- `Settings` 增加 context token 配置。
- `ContextManager` 增加 token 估算。
- 删除外部 `max_messages` 配置，主路径只按 token budget 触发裁剪。

验收：

- 大 tool result 不会挤爆上下文。
- metadata 中能看到 token 统计。
- 长对话压缩后仍保留 initial goal。

### Phase 4: Lightweight Older Memory

目标：在不引入向量数据库的情况下保留旧历史里的少量关键信息。

改动：

- 在 `ContextManager` 内部筛选不在 recent window 中的关键历史。
- 将旧历史压缩成单条 `older_memory`。
- 避免 pinned / older_memory / recent 重复塞入相同信息。

验收：

- 当前问题提到旧文件名或旧错误时，能召回相关历史。
- older_memory token 不超过预算。

### Phase 5: Platform Upgrade Options

当项目继续平台化时再考虑：

- EventLog 从 JSONL 换 SQLite / PostgreSQL。
- Recovery 从启动扫描升级为后台任务。
- Tool 执行从本进程升级为任务队列 worker。
- Shell tool 改为短生命周期 Docker sandbox。
- Context retrieval 改为 embedding + vector store。

## 推荐优先级

最高优先级：

- `EventLog`
- `tool_call_started/succeeded/failed`
- `turn_started/succeeded/failed`
- token-based context budget

中等优先级：

- `RecoveryManager.scan_unfinished`
- tool 幂等元数据
- context stats

低优先级：

- embedding retrieval
- 数据库事件存储
- 多 worker 恢复调度

## 面试表达口径

可以这样介绍优化方向：

```text
我现在的项目是会话级恢复，重启后能根据 conversation_id 继续对话。
但从平台稳定性角度，恢复粒度还不够细，所以我设计了 WAL / event log。
后续每一轮 turn、每次 LLM 调用、每个 tool call 都会写 append-only 事件。
这样服务重启后可以扫描 unfinished task，区分 tool 是 planned、started、succeeded 还是 interrupted。
对于 read_file、ls 这类幂等工具可以自动重放；对于 write_file、run_shell 这类可能有副作用的工具，只记录中断状态并请求用户确认。

上下文方面，我也准备从按消息条数截断升级为 token budget。
因为一条工具输出可能比十几条普通对话更长，只按条数不稳定。
新的方案是 pinned context + older memory + recent window。
首轮目标、未完成任务、关键文件和约束会固定保留，最近上下文按预算保留，旧历史只保留不在 recent window 中的关键信息。
```
