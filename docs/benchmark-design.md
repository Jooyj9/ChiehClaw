# Agent Benchmark Design

目标：为 xxxclaw 建立一套轻量 benchmark，用来回答“这个 Agent 能做什么、稳定性如何、改动是否变好”这三个问题。

本文档只作为后续实现 guide，先不引入复杂评测平台，优先复用当前 `Runner`、JSONL 日志、临时 workspace 和单元测试能力。

## 评测目标

需要覆盖四类能力：

1. Coding task 完成能力：能否按用户目标创建、读取、修改、移动文件。
2. Tool use 稳定性：是否选择正确工具，参数是否合理，失败后是否能继续。
3. Context 管理效果：长对话后是否还能记住首轮目标、关键文件、约束和旧错误。
4. Platform 稳定性：event log、恢复扫描是否能识别中断任务。

暂不追求通用排行榜式 benchmark，优先做和本项目简历、面试追问强相关的工程评测。

## 总体方案

采用两层 benchmark：

```text
Deterministic Runtime Benchmark
+ LLM End-to-End Benchmark
```

### 1. Deterministic Runtime Benchmark

目的：不依赖真实模型，验证平台工程能力。

使用 `MockLLMClient` 或手写 assistant/tool-call 序列，重点测：

- `Runner` 是否正确加载和保存 session。
- tool event 是否完整写入。
- interrupted turn 是否能被 `RecoveryManager` 扫描出来。
- context 裁剪后是否生成 `pinned_context`、`older_memory`、`recent window`。

这层结果应该稳定，适合作为 CI / 单元测试扩展。

### 2. LLM End-to-End Benchmark

目的：验证真实模型在完整 Agent loop 下的任务成功率。

使用真实 Kimi / OpenAI-compatible API，按固定任务集运行：

```text
case prompt
-> Runner.run_turn(...)
-> Agent loop + tool calls
-> inspect workspace / session / logs
-> evaluator 判断通过或失败
```

这层会受模型随机性、网络和费用影响，不要求每次 CI 都跑，适合手动回归或阶段性对比。

## Case 数据结构

建议用 JSONL 或 YAML 保存 case。先用 JSONL，和当前项目日志风格一致。

示例：

```json
{
  "id": "file_create_001",
  "category": "coding_basic",
  "prompt": "在当前目录创建 hello.py，内容是打印 hello xxxclaw",
  "fixture": "empty",
  "expected_files": {
    "hello.py": "hello xxxclaw"
  },
  "forbidden_paths": ["..", "D:/"],
  "max_tool_rounds": 4,
  "tags": ["write_file", "simple"]
}
```

推荐字段：

- `id`：稳定 case id。
- `category`：任务类别。
- `prompt`：用户输入。
- `fixture`：运行前 workspace 初始状态。
- `expected_files`：期望存在的文件和关键内容。
- `expected_absent`：期望不存在的文件。
- `expected_stdout_regex`：如果任务要求执行命令，用正则校验输出。
- `forbidden_paths`：不允许访问或修改的路径。
- `max_tool_rounds`：避免单个 case 卡住。
- `tags`：便于按工具、能力点筛选。

## 初始 Case 集

第一阶段先做 20 个以内，不要贪多。

### A. Basic Workspace

- 创建单文件。
- 读取 README 并回答摘要。
- 修改指定文件中的一行。
- 创建目录并写入多个文件。
- 移动或重命名文件。

### B. Shell Tool

- 执行简单 `python --version` 或 `dir`。
- 运行已有 Python 脚本并读取输出。
- 命令失败后解释 stderr。
- 在指定 cwd 执行命令。

### C. Safety

- 尝试读取 workspace 外路径，期望被拒绝。
- 尝试危险命令，期望不执行或由策略拦截。
- 要求删除不存在文件，期望返回可解释错误。

### D. Context

- 首轮提出目标，中间插入多轮无关短对话，最后要求继续首轮任务。
- 早期提到关键文件，后面大量普通对话，最后追问该文件。
- 早期 tool result 出错，后面追问错误原因。
- 明确约束“不要修改 A 文件”，后续要求改相邻文件。

### E. Recovery

- 构造 `tool_call_started` 无终态事件，验证能扫描 unfinished turn。
- 构造 recoverable read tool 中断，验证能识别可恢复。
- 构造 `run_shell` 中断，验证不会自动重放。
- event log compact 后，未完成任务仍保留。

## 评测运行流程

每个 case 独立运行，避免互相污染。

```text
1. 创建临时 workspace
2. 根据 fixture 初始化文件
3. 创建独立 data_dir
4. 构造 Settings
5. 调用 Runner.run_turn / run_turn_stream
6. 收集 workspace、session、event log
7. evaluator 生成 pass/fail 和原因
8. 输出 JSONL report + Markdown summary
```

建议目录：

```text
benchmarks/
  cases/
    basic.jsonl
    context.jsonl
    recovery.jsonl
  fixtures/
    empty/
    small_python_project/
  reports/
    2026-xx-xx-run.jsonl
    2026-xx-xx-summary.md
```

## 核心指标

### 任务成功率

```text
task_success = passed_cases / total_cases
```

按 category 单独统计，避免一个总分掩盖问题。

### Tool 调用质量

记录：

- 平均 tool call 数。
- 无效 tool call 次数。
- tool 参数错误次数。
- tool 执行失败后恢复成功次数。
- 是否调用了不该调用的工具。

### Context 指标

记录：

- `estimated_total_tokens`
- `pinned_tokens`
- `older_memory_tokens`
- `recent_tokens`
- `trimmed_message_count`
- 关键历史是否进入 `pinned_context` 或 `older_memory`

注意：当前 token 仍是估算值，不能当成真实模型账单；如果模型 API 返回 `usage`，后续再额外记录真实 prompt / completion tokens。

### 稳定性指标

记录：

- turn 是否有完整开始和结束事件。
- tool call 是否都有 started + succeeded / failed / skipped。
- interrupted turn 数量。
- recoverable / non-recoverable 分类是否正确。
- compact 后 event log 是否仍能恢复必要状态。

### 成本和耗时

真实模型 benchmark 记录：

- 单 case latency。
- 总 latency。
- LLM 调用次数。
- 真实 token usage，如果 API 返回。
- 失败 case 的错误类型。

## Evaluator 设计

先用规则 evaluator，不急着引入 LLM-as-judge。

规则 evaluator 支持：

- 文件是否存在。
- 文件内容是否包含关键片段。
- 文件内容是否匹配 regex。
- 禁止路径是否未被创建或修改。
- session metadata 是否包含关键字段。
- event log 是否包含指定事件序列。
- 命令输出是否匹配预期。

LLM-as-judge 只适合评估开放式回答，例如 README 摘要质量；第一阶段可以先不做。

## 对比实验

每次大改可以跑 A/B：

```text
baseline: 当前 main 行为
variant: 新改动行为
```

对比维度：

- 成功率是否提升。
- 平均 tool call 数是否减少。
- context 压缩后任务是否更稳。
- 失败类型是否从“忘记上下文”变成“模型选择不佳”。
- 平均耗时和 token 是否可接受。

## 初始验收标准

第一版 benchmark 不需要追求大而全，达到下面标准即可：

- 至少 15 个 case。
- 覆盖 basic / shell / safety / context / recovery 五类。
- 每个 case 能独立创建临时 workspace 和 data_dir。
- 输出机器可读 JSONL report。
- 输出一份人可读 summary。
- 能在 README 或面试中说明最近一次 benchmark 结果。

## 面试表达口径

可以这样描述：

```text
项目早期我没有做完整 benchmark，后续补了一套轻量评测设计。
它分两层：一层是 deterministic runtime benchmark，不依赖真实模型，验证 Runner、event log、context manager；
另一层是真实 LLM end-to-end benchmark，用固定 coding case 测任务成功率、tool 调用质量、上下文保持能力和恢复能力。
这样可以区分是平台流程问题、工具协议问题，还是模型本身能力问题。
```

## 暂不做的事情

- 暂不接入大型公开 benchmark。
- 暂不做复杂前端看板。
- 暂不引入向量数据库。
- 暂不默认使用 LLM-as-judge。
- 暂不把真实模型 benchmark 放进每次 CI。

这些都可以作为后续平台化增强，而不是第一版 benchmark 的必要条件。
