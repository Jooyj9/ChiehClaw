# xxxclaw Python Guide

目标：在虚拟uv环境下用 Python 逐层复现 `pi-mono` 的核心思路，先做最小可用 coding-agent，再接入飞书。  
原则：非必要不引入。先有可跑主链路，再补外壳。

## 总体路线

按 `pi-mono` 的分层，从下往上做：

1. `pi-ai` 思想：统一模型接口
2. `pi-agent-core` 思想：agent loop + tool call
3. `pi-coding-agent` 思想：session + tools + runtime
4. adapter 层：飞书接入

不要一开始做这些：

- TUI
- 插件市场
- 复杂权限系统
- 多 provider OAuth
- 会话树分支
- 自动 compaction
- 多进程分布式

## 目录结构

```text
xxxclaw/
  README.md
  pyproject.toml
  .env.example
  app/
    main.py
    config.py
    schemas.py

    ai/
      base.py
      models.py
      message.py
      client_openai.py

    agent/
      tools.py
      tool_executor.py
      loop.py
      session.py

    storage/
      session_store.py
      file_store.py

    runtime/
      runner.py
      prompt_builder.py

    adapters/
      base.py
      feishu/
        webhook.py
        router.py
        formatter.py

    utils/
      logger.py
      paths.py

  data/
    sessions/
    logs/
    files/

  tests/
```

## 各目录职责

### `app/ai`

复现 `pi-ai` 的最小思想，只做一件事：统一模型调用。

- `message.py`: 定义统一消息结构
- `models.py`: 定义模型配置
- `base.py`: 定义统一 LLM client 接口
- `client_openai.py`: 先只实现一个 provider

最小接口：

```python
generate(messages, tools=None, system_prompt=None) -> AssistantMessage
stream(messages, tools=None, system_prompt=None) -> iterator[Event]
```

### `app/agent`

复现 `pi-agent-core` 的最小思想：让模型能调用工具并继续下一轮。

- `tools.py`: Tool 定义
- `tool_executor.py`: 真正执行工具
- `loop.py`: agent loop
- `session.py`: 当前会话内存态

最小 loop：

1. 用户消息进入
2. 调模型
3. 若返回 tool call，执行工具
4. 把 tool result 加回消息列表
5. 再调模型
6. 得到最终文本

### `app/storage`

先做最小持久化，不复现 `pi` 的树状 session。

- `session_store.py`: 一个会话一个 JSON 文件
- `file_store.py`: 上传文件、本地产物路径管理

建议先用这种结构：

```text
data/sessions/{conversation_id}.json
data/logs/{conversation_id}.jsonl
data/files/{conversation_id}/...
```

### `app/runtime`

复现 `pi-coding-agent` 的最小产品层。

- `prompt_builder.py`: system prompt、工具说明、上下文拼装
- `runner.py`: 对外暴露 `run_turn(conversation_id, user_input)`

这层负责把：

- `ai`
- `agent`
- `storage`

装成一个可用的 coding-agent 服务。

### `app/adapters/feishu`

最后再做。

- `webhook.py`: 接飞书事件
- `router.py`: 把飞书会话映射到 `conversation_id`
- `formatter.py`: 把 agent 输出转成飞书消息

adapter 只做协议转换，不做 agent 核心逻辑。

## 分阶段实现

### Phase 1: 最小 `pi-ai`

只支持一个 provider，比如 OpenAI 兼容接口。

完成标准：

- 能传入消息数组
- 能拿到 assistant 文本
- 能拿到 tool call 结构

### Phase 2: 最小 `pi-agent-core`

只支持同步 tool loop。

完成标准：

- `read_file`
- `write_file`
- `edit_file`
- `ls`
- `run_shell(bash)`

五种工具可被模型调用。

### Phase 3: 最小 `pi-coding-agent`

加 session 和 runner。

完成标准：

- 按 `conversation_id` 读写历史
- 多轮对话持续可用
- 可以作为本地CLI服务运行

### Phase 4: 飞书 adapter

完成标准：

- 飞书消息进入后路由到 `conversation_id`
- 调用 runner
- 返回最终文本

### Phase 5: 再决定是否补功能

只在真正需要时补：

- streaming
- 自动压缩上下文
- 更细的工具权限
- 多模型切换
- 沙箱

## 核心对象建议

### Message

统一这几类就够了：

- `user`
- `assistant`
- `tool_call`
- `tool_result`

### Tool

字段建议：

- `name`
- `description`
- `parameters_schema`
- `execute()`

### Session

字段建议：

- `conversation_id`
- `system_prompt`
- `messages`
- `metadata`

### Runner

字段建议：

- `llm_client`
- `agent_loop`
- `session_store`
- `tool_registry`

## 最小主链路

```text
Feishu Message
-> adapter.router
-> runtime.runner.run_turn(conversation_id, text)
-> session_store.load()
-> agent.loop.run()
-> ai.client.generate()
-> tool_executor.execute() if needed
-> session_store.save()
-> adapter.formatter
-> Feishu Reply
```

## 实现顺序建议

先写这 6 个文件：

1. `app/ai/message.py`
2. `app/ai/base.py`
3. `app/agent/tools.py`
4. `app/agent/loop.py`
5. `app/storage/session_store.py`
6. `app/runtime/runner.py`

然后做一个最小本地入口：

- `python -m app.main`

先本地跑通，再接飞书。

## 你的约束

写这个项目时始终遵守：

- 先复现思想，不追源码一比一
- 先单 provider，后扩展
- 先单会话模型，后并发池化
- 先同步 loop，后 streaming
- 先本地文件持久化，后数据库
- adapter 最后接，不提前污染核心层

## 一句话落地建议

把 `xxxclaw` 当成 4 层来写：

- `ai` 层统一模型
- `agent` 层处理 tool loop
- `runtime` 层装配 session 与工具
- `adapter` 层最后接飞书

这样可以始终保持最小系统可运行，不会一开始就陷入大而全设计。
