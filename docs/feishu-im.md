# 飞书 IM 长连接接入

当前默认实现已经切到飞书长连接模式，适合本机开发，不需要公网 IP、域名或内网穿透。

实现位置：
- `app/adapters/feishu/long_connection.py`
- `app/adapters/feishu/router.py`
- `app/adapters/feishu/formatter.py`
- `app/adapters/feishu/client.py`

## 当前能力

- 使用飞书长连接接收 `im.message.receive_v1`
- 只处理 `text` 文本消息
- 收到事件后立即提交到后台线程处理，避免阻塞长连接回调
- 继续复用 `Runner.run_turn(conversation_id, user_input)` 主链
- 通过 `POST /im/v1/messages/{message_id}/reply` 回复原消息
- 长连接终端会实时打印飞书侧的输入和 agent 输出，不再需要额外开一个 `app.main` 只为观察主链路

## 为什么改成长连接

长连接更适合本机联调：

- 不需要公网 IP
- 不需要域名
- 不需要内网穿透
- 只要本机能访问公网即可

另外，飞书长连接和 Webhook 一样，收到事件后仍然要尽快完成处理，否则可能触发超时重推。所以代码里保留了“快速接收 + 后台线程执行”的做法。

## 环境变量

先复制 `.env.example`，至少配置这些值：

```env
XXXCLAW_MODEL_PROVIDER=openai
OPENAI_API_KEY=your-model-key
# 或者
MOONSHOT_API_KEY=your-model-key

XXXCLAW_FEISHU_APP_ID=cli_xxx
XXXCLAW_FEISHU_APP_SECRET=xxx
XXXCLAW_FEISHU_BOT_OPEN_ID=ou_xxx
XXXCLAW_FEISHU_WORKER_COUNT=2
XXXCLAW_FEISHU_LOG_LEVEL=INFO
XXXCLAW_FEISHU_GROUP_SESSION_SCOPE=chat_user
XXXCLAW_FEISHU_CONSOLE_STREAM=1
XXXCLAW_FEISHU_SHOW_TOOL_STATUS=0
```

说明：
- `XXXCLAW_FEISHU_BOT_OPEN_ID` 用来在群聊里剥掉机器人的 `@` mention。
- `XXXCLAW_FEISHU_VERIFICATION_TOKEN`、`XXXCLAW_FEISHU_WEBHOOK_PATH` 是旧 Webhook 模式的配置，长连接模式下不需要。
- `XXXCLAW_FEISHU_GROUP_SESSION_SCOPE` 控制群聊会话怎么聚合：
  - `chat_user`：同一群里每个用户各自一条会话
  - `chat`：整群共用一条会话
  - `thread`：按群聊线程拆会话
- 飞书侧使用的 `conversation_id` 是我们本地自己生成的，不是飞书强制规定的。现在已经改成更短、更可读的形式。

## 安装依赖

项目现在按 `uv` 使用依赖管理，先同步环境：

```bash
uv sync
```

如果你只想单独加飞书 SDK，也可以：

```bash
uv add lark-oapi
```

## 启动

```bash
uv run python -m app.adapters.feishu.long_connection
```

或：

```bash
uv run xxxclaw-feishu
```

启动后本机会主动连飞书，不需要监听本地端口。
默认会在当前终端里实时打印：
- 收到的飞书消息
- agent 的流式回复
- 可选的 tool status（开启 `--show-tool-status` 或 `XXXCLAW_FEISHU_SHOW_TOOL_STATUS=1`）

可选参数：

```bash
uv run python -m app.adapters.feishu.long_connection --group-session-scope chat --show-tool-status
```

## 飞书后台配置

1. 打开你的自建应用。
2. 进入事件订阅。
3. 选择“使用长连接接收事件”。
4. 订阅消息事件 `im.message.receive_v1`。
5. 确认应用已发布到对应租户版本。

这一步不再需要：
- Request URL
- URL challenge 校验
- 公网地址

## 接入链路

```text
Feishu Long Connection Event
-> FeishuLongConnectionApp.handle_event_payload()
-> FeishuRouter.route_event_payload()
-> Runner.run_turn(conversation_id, text)
-> AgentLoop / tools
-> FeishuFormatter.format_reply()
-> FeishuAPIClient.reply_message()
```

## 关键实现说明

### 1. 为什么核心主链没有改

飞书 adapter 仍然只依赖一个稳定入口：

```python
runner.run_turn(conversation_id, user_input)
```

所以这次只是把“事件怎么进来”从 Webhook 改成长连接，`Runner`、`AgentLoop`、tools、session 持久化都不需要重写。

### 2. 为什么还保留后台线程

虽然长连接不用公网回调了，但事件处理时限并没有消失。为了避免模型推理或工具执行过慢导致事件超时，这里仍然采用：

- 长连接事件回调里只做解析和投递
- 真正的 agent 执行放到 `ThreadPoolExecutor`
- 同一 `conversation_id` 用锁串行，避免并发写同一份 session

## 建议联调顺序

1. 先本地确认模型链路可用：`uv run python -m app.main --prompt "hello"`
2. 同步依赖：`uv sync`
3. 启动长连接客户端：`uv run python -m app.adapters.feishu.long_connection`
4. 在飞书后台切到“使用长连接接收事件”
5. 给机器人发一条纯文本消息
6. 如果没回复，先看 `data/logs/feishu-long-connection.jsonl`

## 关于 `app.main --conversation-id`

这个参数只影响本地 CLI：

```bash
uv run python -m app.main --conversation-id test01
```

它不会接管飞书消息。飞书消息进入的是 `app.adapters.feishu.long_connection`，由 adapter 自己根据飞书会话信息生成本地 `conversation_id`。

所以：
- 你给 `app.main` 指定 `test01`，只对本地终端对话有效
- 它对飞书侧消息没有直接作用
- 如果你想让飞书会话“更像同一个 session”，应该调的是 `XXXCLAW_FEISHU_GROUP_SESSION_SCOPE`，而不是 `app.main --conversation-id`

## PM2 常驻运行

现在不需要同时启动 `app.main` 和 `long_connection`。

飞书链路长期运行时，只需要长连接进程本身：

```bash
pm2 start ecosystem.config.cjs
pm2 logs xxxclaw-feishu
pm2 restart xxxclaw-feishu
pm2 stop xxxclaw-feishu
```

`pm2` 日志里就能直接看到飞书输入和 agent 输出。

## 当前限制

- 不支持图片、文件、卡片消息
- 不做事件去重
- 不支持 callback subscription，只处理 event subscription
- 多实例部署时，同一个事件只会落到其中一个客户端，这是飞书长连接模式本身的行为

## 参考

- 飞书官方 Python SDK 事件处理文档：[handle-events](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk/handle-events)
- 飞书官方消息回复接口：[message/reply](https://open.feishu.cn/document/server-docs/im-v1/message/reply)
- 飞书官方租户访问凭证：[tenant_access_token_internal](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal)
