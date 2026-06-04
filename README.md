# xxxclaw

按 `xxxclaw-python-guide.md` 落地的最小 Python coding-agent。

当前实现重点：

- `ai`：统一消息结构、OpenAI 兼容 client、mock client
- `agent`：同步 tool loop
- `storage`：按 `conversation_id` 持久化 JSON 会话
- `runtime`：组合 runner，对外暴露 `run_turn`
- `app.main`：本地 CLI 入口
- `streaming`：CLI 默认流式输出
- `tool status`：可选显示 tool-call 阶段的 reasoning 与工具名
- `skills`：按本轮激活技能筛选工具，减少工具 schema 负载
- `context_manager`：pinned context + older memory + 最近窗口 + 大 tool-result 截断
- `app.adapters.feishu`：最小飞书 IM 长连接接入，本机无公网 IP 也可直接联调

## 运行

```bash
uv sync
uv run python -m app.main --prompt "ls ."
uv run python -m app.main
uv run python -m app.main --no-stream --prompt "ls ."
uv run python -m app.main --show-tool-status --prompt "read README.md"
uv run python -m app.adapters.feishu.long_connection
```

未配置 `OPENAI_API_KEY` 时默认使用 `mock` provider，便于本地先跑通主链路。

## 环境变量

复制 `.env.example` 后按需设置：

- `XXXCLAW_MODEL_PROVIDER=mock|openai`
- `OPENAI_API_KEY` / `MOONSHOT_API_KEY`
- `OPENAI_BASE_URL`
- `XXXCLAW_WORKSPACE_ROOT_DIR`
- `XXXCLAW_LLM_TIMEOUT_SECONDS`
- `XXXCLAW_LLM_MAX_RETRIES`
- `XXXCLAW_SHELL_EXECUTION_MODE=local|docker`
- `XXXCLAW_SHELL_SANDBOX_IMAGE`
- `XXXCLAW_CONTEXT_MAX_TOKENS`
- `XXXCLAW_CONTEXT_RECENT_TOKENS`
- `XXXCLAW_CONTEXT_OLDER_MEMORY_TOKENS`
- `XXXCLAW_TOOL_RESULT_MAX_CHARS`
- `XXXCLAW_ASSISTANT_REASONING_MAX_CHARS`
- `XXXCLAW_SKILL_HISTORY_WINDOW_MESSAGES`
- `XXXCLAW_FEATURE_RUNTIME_CONTEXT`
- `XXXCLAW_FEATURE_OLDER_MEMORY`
- `XXXCLAW_FEATURE_RECOVERY`
- `XXXCLAW_FEATURE_WORKSPACE_ISOLATION`
- `XXXCLAW_FEISHU_APP_ID`
- `XXXCLAW_FEISHU_APP_SECRET`
- `XXXCLAW_FEISHU_BOT_OPEN_ID`
- `XXXCLAW_FEISHU_LOG_LEVEL`
- `XXXCLAW_FEISHU_GROUP_SESSION_SCOPE`
- `XXXCLAW_FEISHU_CONSOLE_STREAM`
- `XXXCLAW_FEISHU_SHOW_TOOL_STATUS`

本地 `uv run ...` 会自动读取项目根目录 `.env`；已有系统环境变量优先级更高。

## 测试

```bash
uv run python -m unittest
```

飞书长连接接入步骤见 [docs/feishu-im.md](/D:/agentzero/xxxclaw/docs/feishu-im.md)。

长期运行可以直接用 `pm2` 跑 [ecosystem.config.cjs](/D:/agentzero/xxxclaw/ecosystem.config.cjs)。

Docker 部署说明见 `docs/docker-deploy.md`。

工作区隔离与 shell sandbox 设计见 `docs/sandbox-workspace-guide.md`。

开启 `XXXCLAW_SHELL_EXECUTION_MODE=docker` 前，先构建 sandbox 镜像：

```bash
docker build -f Dockerfile.sandbox -t xxxclaw-sandbox:latest .
```
