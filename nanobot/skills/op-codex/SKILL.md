---
name: op-codex
description: 在本机通过 tmux 调用 codex 执行任务，并按 start / monitor / continue / stop 标准流程管理任务
metadata: {"nanobot":{"emoji":"🌐","os":["linux","darwin"],"requires":{"bins":["tmux","codex","python3"]}}}
---

# op-codex Skill

在本机通过 tmux 启动并控制 codex，用于执行可持续观察、可续推、可恢复的长任务。

该技能采用五种模式运行：
- `start`：启动新任务，创建或复用 tmux 会话，发送初始提示词，并创建 30 秒监控 cron
- `monitor`：由 cron 周期触发，抓取 tmux 输出，分析任务状态，必要时在同一 session 中续推
- `continue`：人工或自动在同一 session 中继续发送新提示词，不创建新的 cron
- `resume`：当已有 session 中追加了新任务或需要恢复监控链路时，复用原状态文件并刷新监控元数据
- `stop`：停止监控，必要时终止 tmux 会话或仅结束监控任务

## 目标

该技能的目标是把 codex 任务管理流程标准化：
1. 规划任务提示词
2. 在指定 tmux session 中运行 codex
3. 使用 30 秒周期监控抓取输出
4. 判断状态：完成 / 报错 / 中断 / 继续执行
5. 在报错或中断时，继续在相同 session 中续推提示词
6. 避免重复创建监控任务
7. 支持在已有 session 上恢复监控链路
8. 在任务结束时停止监控

## 依赖

- tmux
  - codex (系统级 @openai/codex CLI，./codex exec，非交互模式)
- python3

## 核心原则

- **首次启动任务时才允许创建 cron 监控任务**
- **同一个 task_id 同时只能存在一个有效监控 job**
- **报错或中断后的续推必须在原 tmux session 中继续执行**
- **续推不得重复创建 cron 任务**
- **monitor 模式不能重新走 start 流程**，避免递归创建定时任务
- **任务结束后必须停止监控**

## 数据模型建议

建议为每个任务维护一个状态文件，例如：

- `skills/op-codex/runtime/<task_id>.json`

状态文件建议包含：

```json
{
  "task_id": "demo-task",
  "session": "codex-demo-task",
  "workdir": "/home/work/nanobot",
  "mode": "start",
  "status": "running",
  "cron_job_id": "<job_id>",
  "created_at": "2026-03-17T01:45:00+08:00",
  "updated_at": "2026-03-17T01:45:30+08:00",
  "last_output_hash": "<hash>",
  "last_output_excerpt": "<摘要>",
  "last_decision": "continue",
  "allow_auto_continue": true,
  "prompt_goal": "<原始目标>",
  "completion_criteria": "<完成标准>"
}
```

## 四种模式

---

## 1. `start` 模式

### 用途

用于首次启动一个 codex 任务。

### 标准动作

1. 规划任务提示词
2. 生成或确认 `task_id`
3. 检查是否已有对应状态文件
4. 检查目标 tmux session 是否存在
5. 如 session 不存在，则创建 session
6. 启动或复用 session 中的 codex
7. 发送初始提示词
8. 创建状态文件
9. 创建 **每 30 秒执行一次** 的 cron 监控任务
10. 记录 `cron_job_id`

### 提示词规划要求

初始提示词至少包含：
- 任务目标
- 上下文
- 约束条件
- 期望输出
- 完成标准
- 发生错误时的处理原则

建议模板：

```text
目标：<明确任务目标>
上下文：<项目背景、目录、文件、依赖>
约束：<禁止项 / 必须满足项>
输出：<期望结果>
完成标准：<什么算任务完成>
若遇到错误：先分析原因，再尝试安全修复；若无法继续，说明阻塞点。
```

### tmux 建议

会话命名建议：
- `codex-task`
- `codex-<project>`
- `codex-<task_id>`

创建会话：

```bash
tmux new-session -d -s codex-task
```

启动 codex：

```bash
tmux send-keys -t codex-task 'cd /home/work/nanobot && /usr/bin/codex' C-m
```

发送初始提示词：

```bash
tmux send-keys -t codex-task '<初始提示词>' C-m
```

### start 模式约束

- 若发现该 `task_id` 已存在有效监控任务，不得再创建新 cron
- 若 session 已存在且 codex 正在运行，优先复用
- start 模式只在首次启动时使用

---

## 2. `monitor` 模式

### 用途

由 cron 周期触发，用来监控任务状态并决定下一步动作。

### 触发方式

- 推荐使用内置 `cron` 工具
- 周期：`every_seconds=30`

### 标准动作

1. 读取状态文件
2. 根据 `task_id` 找到 tmux session
3. 抓取最近输出
4. 判断输出是否有变化
5. 分析当前状态：完成 / 报错 / 中断 / 继续执行
6. 如需续推，则在**相同 session** 中发送新提示词
7. 更新状态文件
8. 如任务结束，则移除 cron 监控任务

### 输出抓取示例

```bash
tmux capture-pane -pt codex-task
```

建议只分析最近一段输出，并通过 hash 或摘要避免重复分析同一份内容。

### 状态判断规则

#### A. 完成

判定信号示例：
- 输出明确表明任务完成
- 已满足完成标准
- codex 回到空闲状态，且没有后续待执行动作

处理动作：
- 向用户汇报完成
- 更新状态为 `completed`
- 调用 `stop` 流程移除监控 cron

#### B. 报错

判定信号示例：
- 出现 error / failed / traceback / exception
- 命令失败
- 缺少依赖、权限问题、路径错误、语法错误等

处理动作：
- 先分析错误原因
- 若可安全修复，则直接进入 `continue` 子流程，在原 session 中发送修复提示词
- 若不可自动修复，则提醒用户
- 若任务无法继续，则更新状态为 `failed` 并移除 cron

#### C. 中断

判定信号示例：
- tmux session 消失
- codex 进程退出
- 长时间无输出且明显未完成
- 卡在等待输入或出现外部中断

处理动作：
- 判断是否可恢复
- 若可恢复，则进入 `continue` 子流程，在原 session 中发送恢复性提示词
- 若 session 已失效且无法恢复，则提醒用户并停止监控

#### D. 继续执行

判定信号示例：
- 任务仍在进行中
- 有阶段性结果但未达到完成标准
- 可以通过补充指令继续推进

处理动作：
- 保持监控
- 如需要，可进入 `continue` 子流程发送下一步提示词
- 不创建新的 cron

### monitor 模式硬性限制

- **不得再次执行 start 模式中的“创建 cron”动作**
- **不得为同一 task_id 创建第二个监控 job**
- **只能复用原 session**，不能因报错/中断新开并行 session，除非用户明确授权

---

## 3. `continue` 模式

### 用途

用于在已有 tmux session 中向 codex 继续发送提示词。

该模式可由两种情况触发：
- 用户主动要求继续
- monitor 模式检测到报错/中断/可续推状态后自动触发

### 标准动作

1. 读取状态文件
2. 确认 session 仍存在
3. 整理新的续推提示词
4. 将提示词发送到**相同 session**
5. 更新状态文件中的最近动作、最近摘要、更新时间
6. 保持原监控 cron 继续运行

### 续推提示词要求

续推提示词必须：
- 与原任务目标一致
- 针对当前状态有明确动作
- 避免重启整个任务，除非明确必要
- 不创建新的监控任务

示例：

```text
请分析刚才的报错原因，先做安全修复，然后继续执行原任务；不要重新开始整个流程。
```

```text
请基于当前已完成部分，继续完成剩余步骤，并在结束后总结结果。
```

```text
请说明当前中断原因，如果可恢复就继续；否则给出阻塞点和建议。
```

### continue 模式硬性限制

- **不得创建 cron 任务**
- **不得重置 task_id**
- **不得切换到新的 tmux session**，除非用户明确要求
- **必须保留原任务上下文**

---

## 4. `resume` 模式

### 用途

用于在**已有 tmux session 与已有状态文件**的前提下，恢复任务的监控链路。

典型场景：
- 用户已经在现有 codex 会话里追加了新提示词
- 外层流程手动或自动重新创建了监控 cron
- 需要把新的 `cron_job_id`、最近动作与状态写回原状态文件

### 标准动作

1. 读取已有状态文件
2. 确认原 tmux session 仍存在
3. 在原 session 中发送新的提示词
4. 将 `mode` 更新为 `resume`
5. 清空旧的 `last_output_hash` 与 `no_change_count`
6. 写入新的 `cron_job_id`（如果有）
7. 保持后续由原 monitor 流程继续监控

### 约束

- `resume` **不能创建第二个 session**
- `resume` **不能新建第二个状态文件**
- `resume` 适用于“复用旧任务上下文继续做事”，不是重新 start

---

## 5. `stop` 模式

### 用途

用于停止监控任务，必要时结束 session。

### 标准动作

1. 读取状态文件
2. 找到对应 `cron_job_id`
3. 移除监控 cron
4. 更新状态为 `stopped` / `completed` / `failed`
5. 根据需要保留或关闭 tmux session
6. 记录最终输出摘要

### stop 模式的两种常见情况

#### 仅停止监控
适用于：
- 用户不想继续自动监控
- 任务已完成，但用户希望保留 session 查看上下文

#### 停止监控并关闭 session
适用于：
- 任务彻底结束
- 任务失败且不再恢复
- 用户明确要求结束会话

关闭 session 示例：

```bash
tmux kill-session -t codex-task
```

---

## 监控调度建议

### 推荐方式

使用内置 `cron` 工具创建每 30 秒的监控任务。

原则：
- 首次由 `start` 模式创建 cron
- 后续由 `monitor` 模式重复运行
- `continue` 不得创建 cron
- `stop` 负责移除 cron

### 重要说明

可以使用 Python 脚本实现监控逻辑、状态分析和 tmux 输出处理；
但**真正注册周期调度任务**时，应优先使用内置 `cron` 工具。

也就是说：
- Python 负责：状态机、输出分析、hash 去重、摘要生成、决策逻辑
- cron 负责：每 30 秒触发一次 monitor 流程

---

## 推荐目录结构

```text
skills/op-codex/
├── SKILL.md
├── helpers.py
├── monitor_tmux.py
└── runtime/
    ├── <task_id>.json
    └── logs/
```

### 脚本职责

- `start_task.py`
  - 创建或复用 tmux session
  - 启动或复用 session 中的 codex
  - 发送初始提示词
  - 创建 `runtime/<task_id>.json`
  - 可记录外层创建好的 `cron_job_id`
- `monitor_tmux.py`
  - `monitor <task_id>`：抓取 tmux 输出、分析状态、必要时自动续推
  - `continue <task_id> <prompt>`：在原 session 中继续发送提示词，不创建新的 cron
- `resume_task.py`
  - `resume_task.py <task_id> <prompt> --cron-job-id <job_id>`：复用已有状态文件与 tmux session，恢复监控链路
- `stop_task.py`
  - 更新任务状态
  - 按需清空 `cron_job_id`
  - 按需关闭 tmux session
- `helpers.py`
  - 提供状态文件读写、tmux 操作、输出摘要、状态判断、续推提示词生成等辅助能力

### 推荐调用方式

启动任务：

```bash
python3 /home/work/nanobot/nanobot/skills/op-codex/start_task.py \
  --goal '<任务目标>' \
  --context '<上下文>' \
  --constraints '<约束>' \
  --output '<期望输出>' \
  --completion '<完成标准>' \
  --allow-auto-continue
```

监控：

```bash
python3 /home/work/nanobot/nanobot/skills/op-codex/monitor_tmux.py monitor <task_id>
```

人工续推：

```bash
python3 /home/work/nanobot/nanobot/skills/op-codex/monitor_tmux.py continue <task_id> '<prompt>'
```

恢复已有任务的监控链路：

```bash
python3 /home/work/nanobot/nanobot/skills/op-codex/resume_task.py <task_id> '<prompt>' --cron-job-id <job_id>
```

停止任务：

```bash
python3 /home/work/nanobot/nanobot/skills/op-codex/stop_task.py <task_id> --status completed
```

如需同时关闭 session：

```bash
python3 /home/work/nanobot/nanobot/skills/op-codex/stop_task.py <task_id> --status stopped --close-session
```

### 实现说明

当前脚本已支持：
- `start_task.py`
  - 根据 goal 或显式参数生成 `task_id`
  - 创建或复用 tmux session
  - 启动 codex 并发送初始提示词
  - 创建状态文件
- `monitor_tmux.py`
  - 读取 `runtime/<task_id>.json`
  - 检查 tmux session 是否存在
  - 抓取 pane 输出并做 hash 去重
  - 判断 `running / completed / failed / interrupted`
  - 在允许自动续推时，向同一 session 自动发送新的提示词
  - 把监控结果以 JSON 输出，便于外层流程决定是否通知用户或移除 cron
- `stop_task.py`
  - 结束任务状态
  - 视情况清空 `cron_job_id`
  - 视情况关闭 session

如需真正创建或移除 cron，仍应由外层流程根据脚本输出调用内置 `cron` 工具完成。

---

## 任务状态建议

状态建议使用以下枚举：
- `running`
- `waiting`
- `continuing`
- `completed`
- `failed`
- `interrupted`
- `stopped`

最近决策建议使用：
- `no_change`
- `continue`
- `notify_user`
- `stop`

---

## 用户提醒规范

在以下情况应主动提醒用户：
- 任务已完成
- 出现无法自动修复的报错
- 任务被中断且无法恢复
- 需要用户提供额外信息
- 执行时间过长但仍未完成
- **监控结果连续 3 次无明显变化，此时默认按“运行报错 / 卡住”处理，并提醒用户介入**

默认通知规则：
- 如果本轮结果与上一轮相比**没有明显变化**，则**不要主动通知用户**
- 只有在**状态发生变化**、**发生报错**、**发生中断**、**需要用户介入**或**任务完成**时，才通知用户
- 通知内容默认使用**中文**
- 如当前运行渠道为 Feishu，则向当前会话用户发送中文进度或介入提示

连续无变化处理规则：
- 监控流程应记录连续无变化次数
- 若连续 **3 次** 检测结果无明显变化，则默认认为任务可能卡住
- 此时应按“运行报错”处理：
  - 将当前情况标记为需要用户介入
  - 用中文告知用户：任务已连续 3 次无变化，疑似卡住或执行异常
  - 说明当前最新摘要
  - 明确询问用户是否需要继续、调整提示词或终止任务

提醒内容至少包括：
- task_id
- session 名称
- 当前状态
- 关键输出摘要
- 是否仍在监控
- 是否需要用户介入
- 若需要介入，应明确说明用户下一步可以做什么

---

## 本次复盘确认的待修复与已修复项

### 已修复

1. **完成态优先级错误**
   - 问题：任务已完成并保存结果后，因输出连续多次不变化，被误判为 `failed`
   - 修复：`monitor_tmux.py` 现在会优先识别强完成信号（如“保存成功”“已保存到”“保存路径”等），优先判定为 `completed` 并返回 `stop`

2. **长文本发送不稳**
   - 问题：使用 `tmux send-keys` 直接发送多行长文本时，容易出现换行转义、截断、仅进入输入框未稳定提交等情况
   - 修复：`helpers.py` 中的 `tmux_send` 已改为基于 `tmux set-buffer` + `tmux paste-buffer` + `C-m` 的方式发送，更适合长文本和中文内容

3. **continue 模式可补写监控元数据**
   - 修复：`monitor_tmux.py continue` 新增 `--cron-job-id` 参数，便于在继续任务时补写或恢复 `cron_job_id`

### 仍待后续完善

1. **在已有会话中追加新任务后，自动恢复监控链路**
   - 当前现状：需要外层流程手动重新创建 cron
   - 建议方向：补一个专门的“resume/attach”流程，复用旧状态文件并写入新的 `cron_job_id`

2. **stop 流程与状态文件同步清理** ✅ 已修复
   - 修复：`stop_task.py` 默认清空 `cron_job_id`，不再依赖 `--clear-cron` 参数
   - 如需保留 `cron_job_id`（不常见），可传 `--keep-cron`

---

## 推荐 cron 提示模板

创建 `op-codex` 监控 cron 时，建议使用如下模板，并替换其中的 `<TASK_ID>` 与 `<CHAT_ID>`：

```text
请运行 op-codex 监控流程，检查 task_id=<TASK_ID> 对应的 tmux 会话输出，并执行 /home/work/nanobot/nanobot/skills/op-codex/monitor_tmux.py monitor <TASK_ID>。

处理规则：
1. 读取 monitor 返回的 JSON 结果。
2. 如果 decision=no_change，则不要给用户发送任何消息，也不要创建新的 cron。
3. 如果 decision=continue：
   - 表示系统已在原 tmux session 中自动续推
   - 仅当 JSON 中的 user_message 非空且状态相较上次有明显变化时，才用中文向 feishu 用户 <CHAT_ID> 发送 user_message
   - 不要创建新的 cron
4. 如果 decision=notify_user：
   - 用中文向 feishu 用户 <CHAT_ID> 发送 JSON 中的 user_message
   - 如果 user_message 为空，则自行用中文概括当前状态、最新摘要、是否需要用户介入，以及建议用户下一步做什么
   - 不要创建新的 cron
5. 如果 decision=stop：
   - 如果 JSON 中有 user_message，则用中文向 feishu 用户 <CHAT_ID> 发送该消息
   - 然后移除当前 cron 任务
   - 不要创建新的 cron
6. 如果连续监控无变化，monitor 脚本会自行累计 no_change_count：
   - 未达到阈值前，不通知用户
   - 达到 3 次后，会返回 notify_user，并默认按运行报错/卡住处理，需要提醒用户介入
7. 除非状态变化、任务完成、发生报错/中断或需要用户介入，否则不要主动给用户发消息。
8. 严禁创建第二个监控 cron；所有续推都必须复用原 tmux session。

用户信息：
- channel=feishu
- chat_id=<CHAT_ID>
```

当前会话可直接替换为：
- channel=feishu
- chat_id=ou_625a573591d2b39e70bf488c8bebe1e8

---

## 给 nanobot 的执行要求

当用户要求使用 `op-codex` 时，必须先判断当前应该进入哪种模式：

### 若是首次发起任务
使用 `start`：
1. 整理任务提示词
2. 准备或复用 tmux session
3. 启动或复用 codex
4. 发送初始提示词
5. 创建状态文件
6. 创建每 30 秒的 cron 监控任务
7. 保存 `cron_job_id`

### 若是 cron 周期触发
使用 `monitor`：
1. 读取状态文件
2. 抓取并分析 tmux 输出
3. 判断状态
4. 必要时进入 `continue` 子流程
5. 若结束则进入 `stop`

### 若是用户要求继续 / 自动续推
使用 `continue`：
1. 复用原 session
2. 发送新的提示词
3. 保持原 cron 继续运行
4. **不得新增 cron**

### 若任务完成或要求结束
使用 `stop`：
1. 移除 cron
2. 更新状态
3. 按需保留或关闭 tmux session

---

## 明确禁止事项

- 禁止在 `monitor` 或 `continue` 中重复创建 cron 任务
- 禁止同一 `task_id` 同时存在多个监控 job
- 禁止报错后直接重新完整 start，除非用户明确要求重开任务
- 禁止无判断地持续向 codex 发送提示词
- 禁止在高风险、不可逆操作上自动续推，除非得到用户明确授权
