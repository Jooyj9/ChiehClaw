# ChiehClaw Docker 部署

这份文档给的是一套尽量简单、可落地的部署方案，目标是：

- 在 Windows 上用 Docker Desktop 跑起来
- 容器里继续使用 `uv`
- 常驻运行飞书长连接进程
- 将 `data/` 挂载到宿主机，保留 session / log / files

## 方案说明

当前项目最适合容器化的入口是：

```bash
uv run python -m app.adapters.feishu.long_connection
```

原因是飞书长连接已经直接复用了 `Runner` 主链，所以容器里长期运行一个进程就够了，不需要再额外起 `app.main`。

容器化结构如下：

```text
Windows + Docker Desktop (WSL 2 backend)
  -> Docker image
    -> uv sync
    -> app.adapters.feishu.long_connection
  -> bind mount ./data:/app/data
```

## 第一步：安装 Docker Desktop（Windows）

最简单的路径是：

1. 先确保你的 Windows 已启用 WSL 2。
2. 安装 Docker Desktop。
3. 安装完成后确认 Docker Desktop 使用的是 WSL 2 backend。

如果你还没装 Docker Desktop，先不要急着折腾 Linux 虚拟机。对你这个项目来说，Windows + Docker Desktop + WSL 2 是最省事的本地方案。

## 第二步：准备环境变量

在项目根目录创建 `.env`，最少配置这些值：

```env
XXXCLAW_MODEL_PROVIDER=openai
OPENAI_API_KEY=your-model-key
# 或者
MOONSHOT_API_KEY=your-model-key

XXXCLAW_FEISHU_APP_ID=cli_xxx
XXXCLAW_FEISHU_APP_SECRET=xxx
XXXCLAW_FEISHU_BOT_OPEN_ID=ou_xxx

XXXCLAW_FEISHU_GROUP_SESSION_SCOPE=chat_user
XXXCLAW_FEISHU_CONSOLE_STREAM=1
XXXCLAW_FEISHU_SHOW_TOOL_STATUS=0
```

如果你已经在本地 `.env` 里配过，直接复用即可。

## 第三步：构建镜像

在项目根目录执行：

```bash
docker compose build
```

这一步会做几件事：

1. 用 `python:3.13-slim` 作为基础镜像
2. 在镜像里安装 `uv`
3. 拷贝 `pyproject.toml` 和 `app/`
4. 执行 `uv sync`
5. 默认启动飞书长连接客户端

## 第四步：启动容器

前台启动：

```bash
docker compose up
```

后台启动：

```bash
docker compose up -d
```

查看日志：

```bash
docker compose logs -f
```

停止：

```bash
docker compose down
```

## 数据持久化

`compose.yaml` 里已经把本地 `./data` 挂载到了容器内的 `/app/data`：

```yaml
volumes:
  - ./data:/app/data
```

所以这些数据不会随着容器删除而丢失：

- `data/sessions`
- `data/logs`
- `data/files`

## 推荐启动顺序

1. 先在飞书后台确认应用已开启长连接接收事件
2. 在项目目录准备好 `.env`
3. 执行 `docker compose up -d`
4. 用 `docker compose logs -f` 观察启动日志
5. 在飞书里给 bot 发一条测试消息

## 常见问题

### 1. 容器启动了但没回复消息

优先检查：

- `.env` 是否真的被加载
- `OPENAI_API_KEY` / `MOONSHOT_API_KEY` 是否正确
- 飞书后台是否切到了长连接模式
- `docker compose logs -f` 是否有报错
- `data/logs/feishu-long-connection.jsonl` 是否有错误记录

### 2. 为什么没有暴露端口？

因为你现在跑的是飞书长连接，不是 HTTP webhook。容器会主动连接飞书，所以不需要映射端口。

### 3. Shell sandbox 怎么开？

当前这个 Docker 部署只是“把整个 Agent 服务放进容器”。  
如果要让 `run_shell` 使用短生命周期 sandbox 容器，可以配置：

```text
XXXCLAW_SHELL_EXECUTION_MODE=docker
XXXCLAW_SHELL_SANDBOX_IMAGE=xxxclaw-sandbox:latest
XXXCLAW_SHELL_DOCKER_NETWORK=
XXXCLAW_WORKSPACE_ROOT_DIR=/workspaces
```

先在宿主机上构建 sandbox 镜像：

```bash
docker build -f Dockerfile.sandbox -t xxxclaw-sandbox:latest .
```

当前隔离模型是：

- 服务容器负责接飞书和调度
- 每个 conversation 映射到独立 workspace
- sandbox 容器负责真正执行 shell 命令
- workspace 通过 bind mount 挂到 sandbox 的 `/workspace`

注意：如果 Agent 服务本身也跑在容器里，并且还要从服务容器内启动 sandbox 容器，需要额外挂载 Docker socket，并处理 bind mount 的宿主机路径映射；或者改用外部 worker。这个方案更接近平台化，但本地开发时可以先在宿主机直接运行 Agent，再开启 Docker sandbox。

## 你现在可以直接用的命令

```bash
docker compose build
docker compose up -d
docker compose logs -f
```

## 后续可选优化

- 增加 `healthcheck`
- 增加生产环境专用 `compose.prod.yaml`
- 把模型 API 重试策略补上
- 为 `run_shell` 增加 Docker sandbox
