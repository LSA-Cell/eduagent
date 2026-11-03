# Flutter 侧新卡片协议说明（增强机制扩展包）

> 适用范围：Wave C/D 新增的四类卡片 `plan_outline` / `ask` / `todo_list` / `dream_summary`。
> 机器可读契约以 `指导/后端设计/API协议/asyncapi.yaml` 为准（当前 v0.5.0，SSE 事件枚举封闭）。
> 本文只给字段契约与关键 Widget 结构，不含完整 Flutter 代码。

## 通用约定

- 卡片一律经 `card` 事件下发：`{seq, run_id, card_type, card_data, file_urls}`；
  `file_urls` 为 MinIO 预签名 URL（过期 ≤1 小时，渲染前需判空/判过期重取）。
- `ask` 是独立事件（非 card）：`{seq, ask_id, questions[]}`，答复回传
  `POST /v1/ask/answers`（`ask_id + answers`）。
- 所有卡片只读渲染；用户操作只走既定回传通道（confirm / ask answers / 新消息），
  禁止本地改卡片状态回写。

## 1. plan_outline（Plan Mode 大纲卡）

**card_data 契约**

```json
{
  "plan_id": "uuid",
  "version": 1,
  "skill_code": "exam_paper",
  "status": "awaiting_confirm",
  "outline": { "题型分布": {}, "知识点覆盖": [], "逐题标题": [] },
  "confirm_token": "…（30 分钟有效，仅 awaiting_confirm 时出现）",
  "expires_in_s": 1800
}
```

状态机：`drafting → awaiting_confirm → confirmed → generating → completed`，
旁路 `cancelled / expired`；迁移伴随 `plan.status` 事件（客户端据此刷新同一张卡）。

**Widget 结构**

```
PlanOutlineCard
├── Header（skill 图标 + "第 v{version} 版大纲" + PlanStatusChip）
├── OutlineView（按 skill_code 分发）
│     ├── exam_paper → SectionList（题型/知识点/难度配比 + 逐题标题）
│     └── ppt/lesson_plan → PageList（每页/每模块标题+要点）
└── ActionBar（仅 status==awaiting_confirm 可见）
      ├── [确认生成] → POST /v1/plans/{plan_id}/confirm（带 confirm_token）
      ├── [修改]     → 修改意见输入 → POST /v1/plans/{plan_id}/revise（产生新版本卡片）
      └── [取消]     → POST /v1/plans/{plan_id}/cancel
```

注意：`confirm_token` 过期/跨 plan 复用会被 422 拒绝，UI 需引导重新生成；
`status==generating` 时 ActionBar 换为进度态（等待后续交付卡片）。

## 2. ask（结构化提问卡）

**事件契约**（`ask` 事件，`data.questions[]`，每次 ≤3 题）

```json
{
  "ask_id": "…",
  "questions": [{
    "question": "…",
    "multi_select": false,
    "sensitive": false,
    "options": [{
      "label": "选项（≤20 字，推荐项放首位且末尾带（推荐））",
      "description": "…",
      "preview": { "…": "产物类选项的结构化预览（可选）" }
    }]
  }]
}
```

**Widget 结构**

```
AskCard
├── QuestionBlock × N
│     ├── 题干
│     ├── options 无 preview → OptionList（单选 RadioList / 多选 CheckboxList）
│     └── options 含 preview → Row( 左 OptionList | 右 PreviewPane )（并排预览卡）
└── [提交] → POST /v1/ask/answers {ask_id, answers: [{question_index, selected: [label…]}]}
```

交互要点：
- 有 "（推荐）" 后缀的选项默认选中但不自动提交；
- `sensitive=true` 超时按取消处理，客户端本地倒计时 120s 到期后禁用提交并提示
  "已超时取消"；普通题超时由服务端按推荐项继续，客户端收到后续回答中的明示文案即可；
- 简单偏好问题服务端不会带 preview，客户端无需为无 preview 选项留预览位。

## 3. todo_list（AgentTodo 进度卡）

**card_data 契约**

```json
{
  "items": [
    { "content": "梳理知识点覆盖", "status": "completed" },
    { "content": "逐题生成", "status": "in_progress" },
    { "content": "完整性校验", "status": "pending" }
  ]
}
```

约束（服务端保证，客户端只读渲染）：同一时间至多 1 个 `in_progress`；
全部 `completed` 之前不会出现最终交付卡片。

**Widget 结构**

```
TodoListCard
└── ChecklistView（只读）
      └── TodoRow × N：状态图标（✓/⏳/○）+ content
```

禁止用户勾选；卡片随每次 `todo_write` 以新 `card` 事件整体替换（按 seq 去旧）。

## 4. dream_summary（夜间记忆整合摘要卡）

**事件契约**：次日首次进入会话时，`run.start` 之后紧随一条 `dream.summary`
事件：`{seq, stats}`，stats 仅含计数（协议封闭）：

```json
{
  "stats": { "mistakes_organized": 3, "cards_created": 5, "memories_updated": 2 }
}
```

服务端同时以 `card_type=dream_summary` 的卡片语义呈现（stats 已过内容审核，
未过审/无新增则不下发；同一整合结果只推送一次）。

**Widget 结构**

```
DreamSummaryCard
├── Header（🌙 "夜间学习整理"）
├── StatsRow
│     ├── StatItem("整理错题", stats.mistakes_organized)
│     ├── StatItem("新复习卡", stats.cards_created)   ← 角标 "AI 生成"
│     └── StatItem("更新记忆", stats.memories_updated)
└── [去看看] → 跳转复习卡列表（deck=dream-YYYYMMDD，卡片 front 自带"【AI 生成】"）
```

文案由客户端按 stats 本地渲染（服务端默认文案：
"已为你整理昨日 N 道错题，生成 M 张复习卡，更新 K 条学习记忆。"）；
三项全 0 时不渲染本卡（服务端此时不下发事件）。
