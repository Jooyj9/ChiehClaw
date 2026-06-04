# Sandbox Workspace Guide

目标：把 Agent 服务代码和用户代码执行环境隔离开，并为后续多用户场景提供最小可用的 workspace 隔离方案。

本文档作为实现 guide，优先少改现有架构，不引入任务队列、Kubernetes 或复杂权限系统。

## 现状问题

当前 `run_shell` 由 Agent 服务进程直接执行：

```text
Agent service process
-> subprocess.run(command, cwd=workspace_dir)
```

即使 `resolve_workspace_path()` 能限制 cwd 不逃逸，也仍有几个问题：

- Agent 服务和 shell 命令共享同一个运行环境。
- 命令可以影响服务进程可见的环境变量、依赖、临时文件和系统状态。
- 多个用户默认共用一个 workspace，不利于隔离。
- 面试表达上只能说“路径限制”，还不能说“执行环境隔离”。

## 目标形态

拆成两层：

```text
Agent service
  - 接收 CLI / 飞书消息
  - 管理 session / event log
  - 调 LLM 和调度工具

Workspace sandbox
  - 每个 conversation 一个宿主机工作区目录
  - shell tool 可选在短生命周期 Docker 容器内执行
  - 容器只 bind mount 当前 conversation workspace
```

推荐目录：

```text
D:\agentzero\
  xxxclaw\              # Agent 服务代码
  xxxclaw-workspace\    # 用户/会话工作区根目录
    local-cli\
    feishu-p2p-xxx\
    feishu-group-xxx\
```

## 工作区隔离

配置项：

```text
XXXCLAW_WORKSPACE_ROOT_DIR=D:\agentzero\xxxclaw-workspace
```

每轮任务根据 `conversation_id` 映射工作区：

```text
conversation_id
-> conversation_storage_name(conversation_id)
-> workspace_root / storage_name
```

示例：

```text
local-cli -> D:\agentzero\xxxclaw-workspace\local-cli
feishu:p2p:xxx -> D:\agentzero\xxxclaw-workspace\feishu-p2p-xxx
```

这样不同用户、群聊或 thread 的文件默认不会混在一起。

## Docker Sandbox 执行

开启后，`run_shell` 不再直接执行用户命令，而是启动一次性容器：

```text
run_shell(command)
-> docker run --rm
   -v <conversation-workspace>:/workspace
   -w /workspace[/cwd]
   --network none
   --memory 512m
   --cpus 1
   <sandbox-image>
   sh -lc <command>
```

容器销毁后，容器自身临时层会消失；但 `/workspace` 是 bind mount，所以写入工作区的文件会保留在宿主机对应 conversation 目录下。

## 和现在的区别

现在：

```text
Agent process -> subprocess.run -> 当前 workspace
```

优化后：

```text
Agent process -> docker run --rm -> /workspace bind mount -> conversation workspace
```

效果：

- Agent 服务代码目录不再作为默认用户 workspace。
- 每个 conversation 有独立宿主机工作区。
- shell 命令运行在短生命周期容器内。
- 容器只能看到当前 conversation workspace。
- 可以限制网络、CPU、内存和超时。

## 最小实现策略

### Phase 1: Per-conversation Workspace

改动：

- `Settings` 新增 `workspace_root_dir`。
- `Runner` 每轮根据 conversation_id 创建 workspace。
- 工具执行时从当前 turn 的 workspace context 读取工作区。
- `read_file` / `write_file` / `edit_file` / `ls` 都限定在当前 workspace。

验收：

- `case-a` 写入的文件不出现在 `case-b` workspace。
- `../xxxclaw/README.md` 这类路径逃逸被拒绝。

### Phase 2: Shell Execution Mode

配置：

```text
XXXCLAW_SHELL_EXECUTION_MODE=local|docker
```

- `local`：沿用本机 `subprocess.run`，方便开发和单测。
- `docker`：使用短生命周期容器执行命令。

### Phase 3: Docker Sandbox Limits

配置：

```text
XXXCLAW_SHELL_SANDBOX_IMAGE=xxxclaw-sandbox:latest
XXXCLAW_SHELL_DOCKER_NETWORK=
XXXCLAW_SHELL_DOCKER_MEMORY=512m
XXXCLAW_SHELL_DOCKER_CPUS=1
```

先构建 sandbox 镜像：

```bash
docker build -f Dockerfile.sandbox -t xxxclaw-sandbox:latest .
```

`docker run` 会基于镜像创建临时容器；容器不需要提前创建，但镜像必须已存在，或者允许 Docker 自动 pull。

第一版不做复杂 seccomp / capabilities，先把执行环境和 workspace 隔离跑通。网络默认不限制；如果要更安全，可以把 `XXXCLAW_SHELL_DOCKER_NETWORK` 设为 `none`。

## 风险和边界

- Docker 模式依赖本机 Docker Desktop / Docker daemon。
- 如果 `xxxclaw-sandbox:latest` 本地不存在，需要先运行 `docker build -f Dockerfile.sandbox -t xxxclaw-sandbox:latest .`。
- Windows bind mount 路径需要 Docker Desktop 支持。
- `--network none` 会让命令无法访问外网，更安全，但会影响 `pip install` / `curl`。
- bind mount 的 workspace 是持久的，所以危险命令仍可能删除当前 conversation workspace 内的文件。

## 面试表达

可以这样说：

```text
我把 Agent 服务和用户代码执行环境拆开了。
服务进程只负责调度和会话管理；每个 conversation 会映射到 workspace root 下的独立目录。
shell tool 支持 Docker sandbox 模式，每次调用都会启动短生命周期容器，只把当前 conversation workspace bind mount 到 /workspace。
容器销毁后执行环境被丢弃，但 workspace 文件会保留到宿主机对应目录。
这样既实现了服务代码与用户代码隔离，也为多用户工作区隔离打了基础。
```
