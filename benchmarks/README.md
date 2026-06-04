# xxxclaw Benchmark 说明

本目录用于评估 xxxclaw 在核心 Agent 能力上的表现，重点不是追求大规模 benchmark，而是用一组可复现的小 case 验证近期平台优化是否真的有效。

当前 benchmark 主要回答两个问题：

- `current` 相比关闭优化后的 `baseline`，在工作区隔离、上下文管理、恢复机制、工具执行上是否有明确收益。
- Agent 的真实 LLM 闭环是否能完成基本编码、文件编辑、Shell 执行和上下文约束任务。

## 目录结构

```text
benchmarks/
  cases/
    core_cases.json       # 核心测试用例定义
  reports/                # 每次运行生成的 JSONL 明细和 Markdown 汇总
  tmp/                    # benchmark 临时 data/workspace，默认运行后清理
  run_benchmark.py        # benchmark runner
```

## core_cases.json

`cases/core_cases.json` 是 benchmark 的数据源，包含 suite 信息、A/B 变体配置、case 列表和预期指标。

当前共有 18 条 case：

| 类型 | 数量 | 说明 |
|---|---:|---|
| `deterministic_runtime` | 11 | 不依赖 LLM，直接测试工具、上下文构造、恢复扫描等确定性逻辑。 |
| `llm_e2e` | 7 | 调用真实模型，验证从用户输入、LLM 决策、工具调用到最终回复的完整闭环。 |

当前覆盖 5 类核心指标：

| 指标 | 数量 | 关注点 |
|---|---:|---|
| `task_success_rate` | 3 | 基础编码任务是否完成，例如创建文件、修改文件、多文件小项目。 |
| `tool_call_success_rate` | 5 | 工具调用是否成功，包括参数错误、未知工具、Shell 正常和异常执行。 |
| `isolation_safety_pass_rate` | 4 | 是否限制路径逃逸，以及不同会话是否隔离在各自 workspace。 |
| `context_retention_accuracy` | 3 | runtime context、older memory、用户约束是否能被保留和利用。 |
| `recovery_detection_rate` | 3 | event log 能否识别未完成任务、跳过不可自动重放工具、清理旧 event。 |

## Baseline 与 Current

`core_cases.json` 中定义了两个变体：

| 变体 | 含义 |
|---|---|
| `baseline` | 关闭近期优化，近似旧版本行为：关闭 runtime context、older memory、recovery、workspace isolation，并使用本地 Shell。 |
| `current` | 打开当前优化：启用 runtime context、older memory、recovery、workspace isolation，并使用 Docker Shell sandbox。 |

注意：报告中的评分默认按“当前目标能力”打分。因此 baseline 失败不一定代表旧系统完全不可用，而是说明关闭某项优化后，达不到当前平台目标能力。

例如：

- baseline 在 `safety_conversation_workspace_isolation` 失败，说明旧共享 workspace 下 B 会话能读到 A 会话文件。
- baseline 在 `shell_python_version` 的 LLM e2e 中失败，是因为它返回 `execution_mode=local`，不满足当前要求的 Docker sandbox 执行模式。

## Case 字段说明

每条 case 常见字段如下：

| 字段 | 说明 |
|---|---|
| `id` | case 唯一标识，也会出现在报告中。 |
| `category` | 功能类别，例如 `coding_basic`、`shell_tool`、`isolation_safety`。 |
| `metric` | 该 case 归属的评估指标。 |
| `mode` | `deterministic_runtime` 或 `llm_e2e`。 |
| `description` | case 目的说明。 |
| `fixture` | 运行前准备的数据，例如预置文件、会话历史、event log。 |
| `tool_calls` | deterministic 工具调用 case 使用，直接执行工具，不经过模型。 |
| `steps` | 多步骤 deterministic case 使用，例如模拟两个 conversation 的隔离效果。 |
| `turns` | LLM e2e case 使用，表示多轮用户输入。 |
| `expected` | 断言规则，例如文件内容、工具名、错误信息、上下文中应包含的信息。 |
| `tags` | 辅助分类标签，便于后续筛选和扩展。 |

`expected.current` 和 `expected.baseline` 可以分别定义 A/B 变体的预期差异；如果没有单独定义，则使用通用 `expected`。

## Report 文件说明

`reports/` 下每次运行会生成两类文件：

```text
xxxclaw_core_benchmark-<mode>-<timestamp>.jsonl
xxxclaw_core_benchmark-<mode>-<timestamp>.md
```

其中：

- `.jsonl` 是逐 case 明细，一行一个结果，适合定位失败原因和做后续统计。
- `.md` 是汇总报告，适合快速查看指标通过率和失败 case。

JSONL 单条记录通常包含：

| 字段 | 说明 |
|---|---|
| `variant` | `baseline` 或 `current`。 |
| `case_id` | 对应 case 的 `id`。 |
| `metric` | 该 case 计入的指标。 |
| `passed` | 是否通过。 |
| `elapsed_ms` | case 耗时。 |
| `details.failures` | 失败原因列表。 |
| `details.workspace` | 该 case 使用的临时 workspace。 |
| `details.event_types` | LLM e2e 中产生的 event log 类型序列。 |
| `details.tool_names` | LLM e2e 中模型实际规划调用过的工具名。 |
| `details.context_stats` | 上下文构造的统计信息。 |

## 最新结果解读

最新 deterministic 报告：

```text
benchmarks/reports/xxxclaw_core_benchmark-deterministic-20260529-231541.md
```

结果摘要：

| 变体 | 指标 | 结果 |
|---|---|---:|
| baseline | `isolation_safety_pass_rate` | 3/4 |
| baseline | `context_retention_accuracy` | 0/2 |
| baseline | `recovery_detection_rate` | 1/3 |
| baseline | `tool_call_success_rate` | 2/2 |
| current | `isolation_safety_pass_rate` | 4/4 |
| current | `context_retention_accuracy` | 2/2 |
| current | `recovery_detection_rate` | 3/3 |
| current | `tool_call_success_rate` | 2/2 |

该结果说明：workspace isolation、runtime context、older memory、recovery/event log 相关优化在确定性测试中都能被明确验证。

最新 LLM e2e 合并报告：

```text
benchmarks/reports/xxxclaw_core_benchmark-llm_e2e-20260529-232253.md
```

结果摘要：

| 变体 | 指标 | 结果 |
|---|---|---:|
| baseline | `task_success_rate` | 3/3 |
| baseline | `tool_call_success_rate` | 2/3 |
| baseline | `context_retention_accuracy` | 1/1 |
| current | `task_success_rate` | 3/3 |
| current | `tool_call_success_rate` | 3/3 |
| current | `context_retention_accuracy` | 1/1 |

该结果说明：当前版本在真实模型闭环下可以完成基础编码、文件修改、多文件创建、Shell 执行、失败解释和用户约束保留。baseline 唯一失败项是 Shell 执行模式不满足 Docker sandbox 目标能力。

## 常用命令

运行 deterministic A/B：

```powershell
python benchmarks\run_benchmark.py --mode deterministic_runtime --variant all
```

运行真实 LLM e2e A/B：

```powershell
python benchmarks\run_benchmark.py --mode llm_e2e --variant all
```

只运行 current：

```powershell
python benchmarks\run_benchmark.py --mode llm_e2e --variant current
```

只运行单个 case：

```powershell
python benchmarks\run_benchmark.py --mode llm_e2e --variant current --case-id basic_multi_file_project
```

只跑前 N 个 case 做烟测：

```powershell
python benchmarks\run_benchmark.py --mode llm_e2e --variant current --max-cases 1
```

保留临时工作区用于排查失败文件：

```powershell
python benchmarks\run_benchmark.py --mode llm_e2e --variant current --case-id basic_multi_file_project --keep-workdirs
```

## 后续扩展建议

后续如果继续加强 benchmark，可以优先补这几类 case：

- 更复杂的多轮上下文约束，例如先声明禁止修改某文件，几十轮后再执行编辑任务。
- 更真实的恢复场景，例如工具执行中断后重启服务，验证 recoverable tool 自动恢复和 non-recoverable tool 用户确认。
- 更多 Docker sandbox 安全 case，例如危险命令、绝对路径、父目录逃逸和网络访问策略。
- 更细的成本指标，例如 LLM 调用次数、工具调用次数、上下文 token 估算、平均延迟和 P95 延迟。
