# 增强机制扩展包 → 编排层集成说明（Wave C / D1 / D2 全量）

> 本文逐项列出 19 项增强机制对编排层既有模块的改动点，明确**改动**（修改既有
> 文件行为）与**新增**（新文件/新挂载点，旧路径零变化）的边界。红线总表见
> 《EduAgent_增强机制设计_Master_Prompt》§九；SSE 契约见 `指导/后端设计/API协议/asyncapi.yaml`。

## 一、对编排层既有模块的改动点

### Agent Loop（`app/loop/agent_loop.py`）

| 改动 | 性质 | 说明 |
|------|------|------|
| `_base_system_prompt(config, style_tone=None)` | 改动（向后兼容） | §5.3：`style_tone` 非空时追加 `<output_style>` 固定区块（前缀稳定区尾部，确定性纯函数） |
| run.start 之后插入 dream.summary 推送点 | 新增挂载 | §4.2：`deps.dream_summary_provider` 缺省/返回 None 时无任何额外事件 |
| slim / compact / memory_prefetch / model_first_token / tool_exec 五处打点 | 改动（纯增量） | §5.1：`CheckpointTimer` 只调 `time.monotonic()`；`guardrail` 在 executor 内计时；`docgen_wait` 预留 `add_ms` 端口（docgen 轮询在 gen_tasks，当前默认 0） |
| `audit_run` 携带 `timings`（仅终态写入） | 改动（向后兼容） | §5.1：旧 sink 无 timings 字段时按 None 处理 |
| run 收尾后 p95 阈值告警回调 | 新增挂载 | §5.1：`deps.on_checkpoint_alert` 缺省不启用；阈值 `TurnConfig.checkpoint_thresholds_ms` 配置化 |
| Output Style 缓存刷新 | 新增挂载 | §5.3：组装 system prompt 前 `await deps.output_style_refresh()`（60s TTL，fail-soft） |

### 工具执行器（`app/tools/executor.py`）

- 新增可选构造参数 `checkpoint_timer`：护栏链 `guardrails.check` 调用包一层
  `timer.start/stop("guardrail")`（§5.1）。缺省 None，旧测试零影响。

### 异步任务基座（`app/tasks/celery_app.py` / `worker.py`）

- 新增 `beat_schedule`：`dream-nightly-dispatch` 每日 02:00（Asia/Shanghai）触发
  `agent.dream.dispatch`；`task_routes` 把 `agent.dream.run_user` 路由到低优先级
  `dream` 队列；`_TASK_MODULES` 注册 `app.dream.tasks`、`app.hooks.tasks`（新增）。
- 默认队列、既有任务签名、时间限（900s）不变。

### SSE 协议（`app/events/protocol.py` + `指导/后端设计/API协议/asyncapi.yaml`）

- `EventType` 追加 `dream.summary`（v0.5.0；Wave C/D1 的 8 个事件已随前序 Wave 合入）。
- 协议红线保持：新增事件一律先改 AsyncAPI 再写代码；事件体禁止协议外字段
  （dream.summary 的 stats 仅三个计数键，`additionalProperties: false`）。

### 审计与 DDL（`app/audit/` + `infra/postgres/init.sql`）

- `agent_runs` 幂等补列 `timings JSONB`（§5.1）；`write_agent_run` 增加同名参数。
- init.sql 第 12 节新增四表：`dream_runs`（RLS user_id）、`automation_hooks`
  （RLS owner_id）、`feature_gates` / `output_styles`（全局只读，不启用 RLS，
  应用角色仅 SELECT），含索引、output_styles 种子行（ON CONFLICT DO NOTHING）。

### 挂接点（既有产生点的最小改动）

| 事件 | 挂接文件 | 性质 |
|------|----------|------|
| `mistake.created` | `app/tasks/mistake_tasks.py`（错题落库后） | 新增调用，fail-soft |
| `plan.confirmed` | `app/api/plan_mode.py`（确认投递后） | 新增调用，fail-soft |
| `dream.completed` | `app/dream/tasks.py`（整合完成后） | 新增 |

三者都只 `dispatch_event` 投递 Celery 任务，动作执行全部在 worker
（红线：Hook 动作禁止出现在同步请求路径）。

### 既有机制的 gate 接线

- `suggest.maybe_prefetch`（§4.1 speculation）：调用前 `await deps.feature_gate_refresh()`
  （60s 热生效），判定仍走 `deps.feature_gate`（默认 off 红线不变）。
- `DefaultDeps` 新增四个默认装配：`feature_gate`/`feature_gate_refresh`
  （GateRegistry）、`output_style_lookup`/`output_style_refresh`（StyleRegistry）、
  `dream_summary_provider`（summary.make_summary_provider，moderate 过审）、
  `on_checkpoint_alert`（指标占位 + 日志）。全部可选注入，FakeDeps 不受影响。

### 子 Agent / Skill / 压缩管线 / 护栏链

- **零改动**。§3.1 的前缀稳定区约束对 `<output_style>` 区块同样生效
  （排序/内容确定性由 `StyleRegistry.tone_prompt` 纯缓存查找 +
  `format_style_block` 纯函数保证；`cache_watcher` 每请求 hash 对比照常覆盖）。
- Gate/Style/Hook 均不做权限判定，RLS 与护栏链路径不变（红线 §5.2/§5.3）。

## 二、19 项机制 → 代码位置索引

| # | 机制 | 代码位置 | 测试 |
|---|------|----------|------|
| 2.1 | Plan Mode 两段式 | `app/plan/`（manager/state_machine/dispatch/tool/models）、`app/tasks/plan_tasks.py`、`app/api/plan_mode.py` | `tests/test_plan.py` |
| 2.2 | AskUserQuestion | `app/ask/`（tool/store/policy）、`app/api/ask.py` | `tests/test_ask.py` |
| 2.3 | 消息优先级队列与打断 | `app/queue/`（message_queue/inbox）、`app/api/inbox.py`、executor `cancel_running` | `tests/test_interrupt.py` |
| 2.4 | AgentTodo | `app/todo/tool.py`、Loop `card_gate`、`agent_runs.todo_snapshot` | `tests/test_todo.py` |
| 2.5 | Stop Hooks | `app/stop_hooks/`（service/base/validators/models） | `tests/test_stop_hooks.py` |
| 3.1 | Prompt-Cache 感知压缩 | `app/context/cache_aware.py`、Loop `guarded_apply`/`cache_watcher` | `tests/test_cache_aware.py` |
| 3.2 | ToolUseSummary | `app/tools/summary.py`、executor `summary_stats` | `tests/test_builtin_tools.py` 等 |
| 3.3 | Denial Tracking | `app/denial/` | `tests/test_denial.py` |
| 3.4 | Cost Tracker | `app/cost/`（ledger/pricing/models） | `tests/test_cost.py` |
| 4.1 | PromptSuggestion | `app/suggest/`（generator/models）、Loop run.end 后挂接 | `tests/test_suggest.py` |
| 4.2 | DreamTask 夜间整合 | `app/dream/`（pipeline/locks/tasks/summary/models）、beat_schedule、Loop dream.summary 挂载 | `tests/test_dream.py` |
| 4.3 | 工具延迟加载 | `app/tools/search.py`、`configure_lazy_loading` | `tests/test_tool_search.py` |
| 5.1 | 分段 Checkpoint | `app/observe/checkpoint.py`、Loop 打点、`agent_runs.timings` | `tests/test_checkpoint.py` |
| 5.2 | Feature Gates | `app/gates/`（registry/models）、`DefaultDeps.feature_gate` | `tests/test_gates.py` |
| 5.3 | Output Styles | `app/styles/`（registry/models）、`_base_system_prompt` | `tests/test_styles.py` |
| 5.4 | Hooks 与降级 | `app/hooks/`（registry/dispatcher/fallbacks/tasks/models）+ 三处挂接点 | `tests/test_hooks.py` |

（19 项中另含编排层基线机制——四级压缩、记忆注入等——见 `app/context/`、
`app/memory/`，不在本扩展包 16 项增量内重复列入。）

## 三、部署注意

- dream 低优先级队列：`agent-worker` 需消费 `agent,dream` 两个队列；beat 由
  `agent-beat` 服务（`celery beat`）承载（见 docker-compose.yml）。
- `feature_gates`/`output_styles` 的增改走运维/运营迁移通道（应用角色只读），
  60s 内热生效，无需重启。
- DreamTask 只在 worker 异步路径存在（beat → dream 队列），API 进程无任何
  整合入口；推送走次日首个会话的 SSE 流。
