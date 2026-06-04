# Workspace 多 Session 指导文档

## 目标

在不改变现有 workspace 隔离方式的前提下，让同一个飞书路由或 CLI workspace 保存并切换多个独立 session。

```text
飞书路由 / CLI conversation_id
└── workspace
    ├── 当前 session
    ├── 历史 session
    └── 新 session
```

`/new` 和 `/resume` 在进入 Agent Loop 前由 Runner 处理，因此命令文本、切换提示都不会写入 session 历史，也不会调用 LLM。

## 标识拆分

旧实现中，一个 `conversation_id` 同时标识 workspace、session 和 event log：

```text
conversation_id -> workspace + session JSON + event log
```

现在将其拆成两层：

```text
workspace_id -> session tree -> active session_id

workspace_id -> workspace 目录
session_id   -> session JSON、event log、turn log
```

对外传入 Runner 的 `conversation_id` 继续作为 `workspace_id`，因此无需修改飞书 Router 和 CLI 参数。不同 session 共享工作区文件，但消息历史、event log 相互独立。

## 命令行为

### `/new` 或 `/clear`

- 在当前 workspace 下创建空 session 节点。
- 立即持久化空 session，并切换活动指针。
- 返回切换提示，不调用 LLM，不写入历史。

### `/resume`

- 返回当前 workspace 下的 session 列表。
- 标记当前活动 session。
- 展示序号、标题、更新时间和 session ID。

### `/resume <序号|session_id>`

- 将活动指针切换到指定 session。
- 下一条普通用户消息继续使用该 session 的历史。
- 不能通过 session ID 切换到其他 workspace 的节点。

## 持久化结构

session tree 索引单独保存在：

```text
data/session_trees/<workspace-storage-name>.json
```

```json
{
  "workspace_id": "feishu-p2p-user",
  "active_session_id": "feishu-p2p-user--s-20260604T120000Z-a1b2c3",
  "sessions": [
    {
      "session_id": "feishu-p2p-user",
      "title": "Existing session",
      "created_at": "2026-06-04T11:00:00+00:00",
      "updated_at": "2026-06-04T11:30:00+00:00"
    }
  ]
}
```

首次运行时若不存在索引，原 `conversation_id` 对应的旧 session 会自动成为第一个节点，保持已有数据兼容。

当前实现是轻量 session 列表，不涉及会话分支、历史复制或回滚；`/new` 始终创建空 session。

## 执行链路

```text
飞书 / CLI 消息
-> Runner._dispatch_request(workspace_id, user_input)
-> 若为 session 命令：直接操作 session tree 并返回
-> 否则解析 active_session_id
-> 使用 workspace_id 进入原 workspace
-> 使用 session_id 加载历史、记录 event log、执行 Agent Loop
```

## 恢复与隔离

- event log 按 `session_id` 保存，因此 unfinished turn 会恢复到正确的历史。
- 每个 session 的 metadata 保存 `workspace_id`，恢复时据此重新进入共享 workspace。
- File 和 Shell 工具仍只接收由 `workspace_id` 派生的工作区。
- 新建或切换 session 不会创建、移动或删除 workspace 文件。

## 验证重点

- `/new` 后新 session 历史为空，旧 session 历史不变。
- `/new`、`/resume` 及系统提示不进入任一 session 历史。
- 新旧 session 能读写同一个 workspace 文件。
- `/resume <序号|session_id>` 只能选择当前 workspace 下的节点。
- Runner 重启后仍能恢复活动 session 指针。
