# 存档操作

记录工具为相对于当前 Skill 根目录的 `scripts/archive.py`。先确定该脚本的实际路径，使用可用的 Python 3 运行。所有输入为 UTF-8 JSON 文件；使用文件工具生成输入，避免把用户原文拼入 shell 命令。输入临时文件可放在当前允许写入的工作目录。

默认数据位置由脚本确定：当前用户主目录下 `Documents/Codex/pomodoro-records`。所有每日新对话沿用同一个位置。测试必须提供隔离的 `--data-dir`，不把模拟记录写入用户使用数据。

存档使用追加事件文件 `events.jsonl`。脚本负责校验、锁定、重复请求防重和读取；不要绕开脚本修改历史事件。参数或存档路径问题属于实现问题，不要求用户自己排查。

## 命令

以下命令中的脚本路径及输入文件路径换成当前实际路径。不要把这些操作步骤当作每次安排必须向用户展示的内容。

```text
python scripts/archive.py context --date YYYY-MM-DD
python scripts/archive.py plan --input plan.json
python scripts/archive.py feedback --input feedback.json
python scripts/archive.py review --date YYYY-MM-DD --task-class writing
python scripts/archive.py review --from-date YYYY-MM-DD --to-date YYYY-MM-DD --limit 100
python scripts/archive.py revision --input revision.json
```

脚本提供 `--help` 说明实际选项；新增或修改操作前可核对帮助。日期取真实用户当地日期，明确的其他日期使用用户提供值。存档损坏或无法写入时，停止存档操作并明确报告失败，不吞掉坏行、不宣布已保存。

## 计划

使用唯一稳定的 `plan_id`，例如当天日期加本次窗口唯一标识；同一请求重试沿用原 ID。一个窗口的多阶段安排记为一份计划；用户后来另给时间或确实调整安排时再建新计划。不为同一窗口的每个阶段重复创建整窗计划，也不事后调整原计划以迎合结果。

必填字段：

- `plan_id`、`date`、`skill_version`（读取当前入口版本）。
- `priority`：`A`、`B`、`habit` 或 `other`，与本轮实际选择对应。
- `goal`：本轮服务的目标。
- `stage`、`acceptance`、`start_action`、`key_point`：当前实际安排。

其他字段：`task_class` 为粗粒度类别或 `null`，`estimated_minutes` 为真实估计或 `null`，`parameters` 保存当时的 `work_minutes` 与 `planning_ratio`。

从0.2.1开始，`work_minutes` 是本窗口扣除用户预留休息和已知经过时间后的净可用分钟，`estimated_minutes` 为所有已安排阶段的估时合计，约为净可用时间的60%。每次新计划必须显式提供 `parameters`，不能使用脚本旧默认25。旧版本记录仍按对应旧版语义理解，不重写历史。脚本要求 `work_minutes` 为正整数；若净时间包含小数，向下取整并在关键点保留原值。没有至少一分钟净时间时不创建工作计划。

`stage` 写明“整窗安排”及有序阶段和估时，`acceptance` 写各阶段成果；这两项在 `context` 摘要可见。`priority` 取窗口主目标的优先级，`goal` 和 `stage` 可注明混合的承诺事项或B；反馈分别说明各阶段进展。`key_point` 保留总窗口、休息次数/总量、净时间、预算和条件性后续阶段，`start_action` 为当前第一步。休息未说明时先确认，不生成猜测参数的计划。不新增脚本未支持字段，不要求用户补填表单。

例如一份计划输入的形状：

```json
{
  "plan_id": "2026-01-01-stage-unique",
  "date": "2026-01-01",
  "skill_version": "0.2.1",
  "priority": "A",
  "goal": "推进已确定的写作任务",
  "task_class": "writing",
  "stage": "整窗安排：确定核心论点12分钟；写一个论证段落20分钟；核对并修改16分钟",
  "acceptance": "有核心论点、支持它的段落，以及核对修改后的草稿",
  "start_action": "打开已有草稿，写下读者最需要得到回答的问题",
  "key_point": "总窗口90分钟，用户预留两次5分钟休息，净可用80分钟，任务预算48分钟；按顺序执行，计时与休息位置由用户决定",
  "estimated_minutes": 48,
  "parameters": {"work_minutes": 80, "planning_ratio": 0.6}
}
```

例子只说明字段形状；不得把例子的日期、目标、成果写入真实使用记录。

## 反馈

关联已有 `plan_id`；用唯一稳定的 `event_id` 防止重试重复记录。用户后来更正进度或用时，创建新的反馈事件，通过 `correction_of` 指向先前反馈，保留历史。

```json
{
  "event_id": "feedback-unique",
  "plan_id": "2026-01-01-stage-unique",
  "progress": "用户报告的实际进展",
  "completed": null,
  "actual_minutes": null,
  "interruptions": null,
  "obstacle": null,
  "hypotheses": null,
  "improvement_candidates": null
}
```

- `progress` 必填，保留用户真正报告的进展，不加未经证实的成功结论。同一窗口多次反馈时保存累计进展快照，带上仍正确的已知字段；脚本取最后一条为最新反馈，不自动继承之前字段。
- `completed` 对应整个窗口的已定计划；用户只完成其中一个阶段时，在进展中注明，不推定整份完成。仅明确报告整份完成或未完成时填 `true` 或 `false`，否则 `null`。
- `actual_minutes` 只用用户报告的本计划实际投入合计；只报告某阶段用时就写入进展，不自动当作整窗实际用时。没有合计时为 `null`。总窗口、净可用分钟、墙钟余量和 AI 等待用时均不能自动填成实际专注用时。如果只提供模糊用时，就在进展中注明模糊说法，不制造精确数字。
- `interruptions`、`obstacle` 记录明确说明的情境，不把中断时间混入核心投入。
- `hypotheses`、`improvement_candidates` 保存字符串列表，明确它们只是待验证解释和建议。未识别时为 `null`。

不为补齐这些字段额外问卷。缺失信息会改变下一步时，才继续 grill-me。

修正反馈是完整快照。若已知且仍然正确的用时或其他字段需要保留，显式带入这些值；未提供的字段会归为 `null`，不会自动从旧反馈继承。

## 查看与修订

`context` 读取当天记录、待反馈情况及当前默认参数。它不替用户决定新一天的 A/B。

`review` 按用户关心的日期或类别取相关计划和最新反馈；无日期条件时覆盖全部日期，支持 `--from-date`、`--to-date` 范围。默认最多显示 20 条，但数量统计覆盖筛选范围；`truncated` 为真时不能声称已读全部案例，按类别、日期分段或调整 `--limit` 读取。不只抽失败，不从消息间隔计算时间误差。不要在每次安排时读入所有历史。比较跨版本记录时区分0.2.0及以前的单段计时与0.2.1起的整窗预算，不能直接合并比较。

长期默认改变经双方确认后，用 `revision` 追加 `event_id`、`date`、`skill_version`、`confirmed: true`、`summary`、`basis`、`reason`。参数变化还提供完整的 `old_parameters` 和 `new_parameters`；旧参数必须与当前默认吻合。脚本拒绝没有确认或旧值冲突的修订。

脚本记录的参数修订会成为后续 `context` 返回的默认值。指令规则改变需要实际修改 Skill 文件并更新版本；仅追加修订日志不会自动修改指令。详细流程见 [迭代指引](improve.md)。
