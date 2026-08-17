-- ============================================================================
-- EduAgent 数据库初始化脚本
-- 挂载位置：/docker-entrypoint-initdb.d/init.sql（postgres 容器首次启动时执行）
-- 数据库：PostgreSQL 15 + pgvector
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 0. 扩展
-- ----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector：向量存储与检索
CREATE EXTENSION IF NOT EXISTS pgcrypto;    -- gen_random_uuid() 等加密函数

-- ============================================================================
-- 1. 用户与密钥
-- ============================================================================

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone             VARCHAR(20) UNIQUE,               -- 手机号（注册主标识之一）
    password_hash     VARCHAR(255),                     -- 密码哈希（bcrypt）
    email             VARCHAR(255),                     -- 邮箱（可选）
    grade_level       VARCHAR(50),                      -- 学段：primary/junior/senior/university
    default_subject   VARCHAR(50),                      -- 默认学科
    is_teacher        BOOLEAN NOT NULL DEFAULT false,   -- 是否教师身份
    is_paid           BOOLEAN NOT NULL DEFAULT false,   -- 是否付费用户（限流分层用）
    user_mode         VARCHAR(20) NOT NULL DEFAULT 'student',  -- 模式：student/teacher/parent
    teacher_verified_at TIMESTAMPTZ,                      -- 教师资质认证通过时间
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 用户自带大模型 API Key（AES-256-GCM 加密存储，永不回显明文）
CREATE TABLE IF NOT EXISTS user_api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider        VARCHAR(50) NOT NULL,               -- openai/anthropic/qwen/...
    encrypted_key   TEXT NOT NULL,                      -- AES-256-GCM 密文（base64）
    monthly_budget  BIGINT,                             -- 月度额度（单位：USD 美分，NULL=不限）
    used_budget     BIGINT NOT NULL DEFAULT 0,          -- 当月已用额度（USD 美分）
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_user_api_keys_user_provider UNIQUE (user_id, provider)
);

-- ============================================================================
-- 2. 聊天与消息
-- ============================================================================

-- 聊天会话
CREATE TABLE IF NOT EXISTS chat_sessions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title             VARCHAR(200),                     -- 会话标题（首条消息自动生成）
    subject           VARCHAR(50),                      -- 学科
    model             VARCHAR(100),                     -- 使用的模型标识
    inference_source  VARCHAR(20),                      -- 推理来源：internal（自有 vLLM）/ external（用户 Key）
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 消息（parent_id 支持分支对话树）
CREATE TABLE IF NOT EXISTS messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    parent_id       UUID REFERENCES messages(id) ON DELETE SET NULL,  -- 可空；分支对话的父消息
    role            VARCHAR(20) NOT NULL,               -- user/assistant/system/tool
    content_type    VARCHAR(20) NOT NULL DEFAULT 'text',
                    -- text/image/audio/file/tool_result/ppt_preview/lesson_plan_preview/exam_preview
    content         TEXT,                               -- 文本内容或结构化 JSON 字符串
    metadata        JSONB,                              -- 附加信息（token 用量、工具调用参数等）
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 3. 学习数据（错题 / 记忆卡片 / 学习计划）
-- ============================================================================

-- 错题本（列定义以 shared/eduagent_shared/models.py 为唯一事实源）
CREATE TABLE IF NOT EXISTS mistake_records (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject           VARCHAR(50),
    question          TEXT NOT NULL,                    -- 题目文本（OCR/手动录入）
    wrong_answer      TEXT,                             -- 用户作答
    correct_answer    TEXT,                             -- 正确答案
    analysis          TEXT,                             -- AI 解析
    knowledge_points  JSONB NOT NULL DEFAULT '[]',      -- 知识点标签数组
    mastery           FLOAT NOT NULL DEFAULT 0.0,       -- 掌握度 0..1
    source            VARCHAR(50),                      -- 来源（chat/manual/...）
    review_count      INTEGER NOT NULL DEFAULT 0,       -- 已复习次数
    next_review_at    TIMESTAMPTZ,                      -- 下次复习时间（艾宾浩斯曲线驱动）
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 记忆卡片（SM-2 间隔重复参数）
CREATE TABLE IF NOT EXISTS flashcards (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject         VARCHAR(50),
    deck            VARCHAR(100) NOT NULL DEFAULT 'default', -- 卡组
    front           TEXT NOT NULL,                      -- 正面（问题）
    back            TEXT NOT NULL,                      -- 背面（答案）
    ease_factor     FLOAT NOT NULL DEFAULT 2.5,         -- SM-2 难度系数
    interval_days   INTEGER NOT NULL DEFAULT 0,         -- 当前间隔（天）
    repetitions     INTEGER NOT NULL DEFAULT 0,         -- 连续正确次数
    next_review_at  TIMESTAMPTZ,                        -- 下次复习时间
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 学习计划
CREATE TABLE IF NOT EXISTS study_plans (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title        VARCHAR(200) NOT NULL,
    subject      VARCHAR(50),
    goal         TEXT,                                  -- 目标描述
    start_date   DATE,
    end_date     DATE,
    status       VARCHAR(20) NOT NULL DEFAULT 'active', -- active/completed/archived
    plan_content JSONB NOT NULL DEFAULT '{}',           -- AI 生成的日程安排
    progress     FLOAT NOT NULL DEFAULT 0.0,            -- 完成进度 0..1
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 4. 知识库（RAG）
-- ============================================================================

-- 知识库文档（用户上传的资料）
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       VARCHAR(200) NOT NULL,
    file_name   VARCHAR(255),
    file_type   VARCHAR(50),                            -- pdf/docx/txt/md/...
    object_key  VARCHAR(500),                           -- MinIO 对象键
    size_bytes  BIGINT,
    subject     VARCHAR(50),
    status      VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending/processing/ready/failed
    chunk_count INTEGER NOT NULL DEFAULT 0,
    metadata    JSONB NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 文档切片 + 向量（embedding 维度 1536，与所选 embedding 模型对齐）
CREATE TABLE IF NOT EXISTS kb_chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content     TEXT NOT NULL,
    embedding   VECTOR(1536),
    metadata    JSONB NOT NULL DEFAULT '{}',            -- 章节标题、页码、段落序号等
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 5. 长期记忆
-- ============================================================================

CREATE TABLE IF NOT EXISTS memories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    memory_type VARCHAR(30) NOT NULL,                   -- profile/preference/weak_point/event
    content     TEXT NOT NULL,
    importance  FLOAT NOT NULL DEFAULT 0.5,
    embedding   VECTOR(1536),
    metadata    JSONB NOT NULL DEFAULT '{}',            -- 来源会话等扩展信息
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 6. 内容生成产物（教案 / PPT / 试卷 / 学情分析）
-- ============================================================================

-- 教案（列定义以 shared/eduagent_shared/models.py 为唯一事实源）
CREATE TABLE IF NOT EXISTS lesson_plans (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title            VARCHAR(200) NOT NULL,
    subject          VARCHAR(50),
    grade_level      VARCHAR(50),
    duration_minutes INTEGER,                           -- 课时长度（分钟）
    objectives       JSONB NOT NULL DEFAULT '[]',       -- 教学目标数组
    content          JSONB NOT NULL DEFAULT '{}',       -- 教学环节结构化内容（_render 等为内部保留键）
    status           VARCHAR(20) NOT NULL DEFAULT 'draft', -- draft/published
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- PPT 演示文稿（生成文件存 MinIO，object_key 为对象键）
CREATE TABLE IF NOT EXISTS presentations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       VARCHAR(200) NOT NULL,
    subject     VARCHAR(50),
    grade_level VARCHAR(50),
    theme       VARCHAR(100),                           -- 模板主题
    slide_count INTEGER NOT NULL DEFAULT 0,
    outline     JSONB NOT NULL DEFAULT '{}',            -- 幻灯片大纲（slides/topic + _thumbnails/_error 内部保留键）
    object_key  VARCHAR(500),                           -- MinIO 对象键（pptx）
    status      VARCHAR(20) NOT NULL DEFAULT 'draft',   -- draft/generating/ready/failed
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 试卷（questions 为结构化题目数组，answer_key 可选）
CREATE TABLE IF NOT EXISTS exam_papers (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title            VARCHAR(200) NOT NULL,
    subject          VARCHAR(50),
    grade_level      VARCHAR(50),
    total_score      INTEGER,                           -- 总分
    duration_minutes INTEGER,                           -- 建议时长（分钟）
    questions        JSONB NOT NULL DEFAULT '[]',       -- 题目结构化内容（题型/题干/选项/答案/解析）
    answer_key       JSONB,                             -- 可空：答案速查表
    status           VARCHAR(20) NOT NULL DEFAULT 'draft',
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 学情分析（周期性的学习数据洞察）
CREATE TABLE IF NOT EXISTS learning_analyses (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE, -- 发起分析的用户（教师端）
    analysis_type VARCHAR(30) NOT NULL,                 -- weekly/monthly/custom
    period_start  DATE,
    period_end    DATE,
    summary       TEXT,                               -- 报告摘要
    metrics       JSONB NOT NULL DEFAULT '{}',        -- 指标：正确率/时长/薄弱点等
    suggestions   JSONB NOT NULL DEFAULT '[]',        -- 教学建议数组
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 7. 索引
-- ============================================================================

-- 用户与密钥
CREATE INDEX IF NOT EXISTS idx_user_api_keys_user_id   ON user_api_keys(user_id);

-- 会话与消息
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_created ON chat_sessions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session_created   ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_parent_id         ON messages(parent_id);

-- 学习数据
CREATE INDEX IF NOT EXISTS idx_mistake_records_user_created    ON mistake_records(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mistake_records_next_review     ON mistake_records(user_id, next_review_at);
CREATE INDEX IF NOT EXISTS idx_mistake_records_knowledge_points ON mistake_records USING GIN(knowledge_points);
CREATE INDEX IF NOT EXISTS idx_flashcards_user_due             ON flashcards(user_id, next_review_at);
CREATE INDEX IF NOT EXISTS idx_study_plans_user_created        ON study_plans(user_id, created_at DESC);

-- 知识库与记忆
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_user_created ON knowledge_documents(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_document_id            ON kb_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_memories_user_created            ON memories(user_id, created_at DESC);

-- 生成产物
CREATE INDEX IF NOT EXISTS idx_lesson_plans_user_created        ON lesson_plans(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_presentations_user_created       ON presentations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_exam_papers_user_created         ON exam_papers(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_learning_analyses_user_created   ON learning_analyses(user_id, created_at DESC);

-- ----------------------------------------------------------------------------
-- 向量索引（ivfflat，余弦距离）
-- 注意：ivfflat 索引基于聚类，表中数据量过少（< lists*10 行）时召回率差。
-- 生产环境建议在导入一定量数据后再创建，或改用 CONCURRENTLY 在线重建：
--   CREATE INDEX CONCURRENTLY idx_kb_chunks_embedding ON kb_chunks
--       USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
-- 此处直接创建，保证开箱可用；数据增长后可 REINDEX 或按上述方式重建。
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_kb_chunks_embedding ON kb_chunks
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_memories_embedding ON memories
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ============================================================================
-- 8. 行级安全（RLS）
-- ----------------------------------------------------------------------------
-- 隔离机制：应用层每个请求/连接设置
--   SET app.current_user_id = '<用户UUID>';   （或 set_config(...)）
-- 策略通过 NULLIF(current_setting('app.current_user_id', true), '')::uuid 判定归属：
--   · 第二个参数 true —— GUC 未定义时返回 NULL 而非报错；
--   · NULLIF(..., '') —— 会话内 RESET 后自定义 GUC 的值为 '' 而非 NULL，
--     统一归一为 NULL，避免 ''::uuid 抛错。NULL 使所有策略判定为假，
--     即「未设置身份 = 不可见任何行」，默认拒绝。
--
-- 服务角色 / 管理旁路：
--   1) 超级用户 postgres 默认绕过 RLS（不建议业务使用）。
--   2) 建议创建专用服务角色并授予 BYPASSRLS，用于迁移脚本、跨用户统计等
--      受信场景（务必仅限内网、审计其使用）：
--        CREATE ROLE eduagent_admin LOGIN PASSWORD '...' BYPASSRLS;
--      或对既有角色：ALTER ROLE eduagent_admin BYPASSRLS;
--   3) 普通应用账号（eduagent）必须走 RLS，不得授予 BYPASSRLS。
-- ============================================================================

-- ---------- 直接含 user_id 的表：本人可见可写 ----------
DO $$
DECLARE
    t TEXT;
    user_tables TEXT[] := ARRAY[
        'user_api_keys', 'chat_sessions', 'mistake_records', 'flashcards',
        'study_plans', 'knowledge_documents', 'memories', 'lesson_plans',
        'presentations', 'exam_papers', 'learning_analyses'
    ];
BEGIN
    FOREACH t IN ARRAY user_tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',  t);
        EXECUTE format(
            'CREATE POLICY %I ON %I FOR ALL
             USING (user_id = NULLIF(current_setting(''app.current_user_id'', true), '''')::uuid)
             WITH CHECK (user_id = NULLIF(current_setting(''app.current_user_id'', true), '''')::uuid)',
            t || '_owner_isolation', t);
    END LOOP;
END $$;

-- ---------- messages：通过所属 session 的 user_id 判定归属 ----------
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages FORCE ROW LEVEL SECURITY;

CREATE POLICY messages_owner_isolation ON messages FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM chat_sessions s
            WHERE s.id = messages.session_id
              AND s.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM chat_sessions s
            WHERE s.id = messages.session_id
              AND s.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

-- ---------- kb_chunks：通过所属 document 的 user_id 判定归属 ----------
ALTER TABLE kb_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb_chunks FORCE ROW LEVEL SECURITY;

CREATE POLICY kb_chunks_owner_isolation ON kb_chunks FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM knowledge_documents d
            WHERE d.id = kb_chunks.document_id
              AND d.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM knowledge_documents d
            WHERE d.id = kb_chunks.document_id
              AND d.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

-- ---------- users：仅本人可读本人记录；INSERT 由注册流程以服务角色执行 ----------
-- 说明：注册时用户尚不存在，无法通过「本人」策略，故用户创建走 BYPASSRLS 的
-- 服务角色（或安全 definer 函数）；登录后仅允许读取/更新本人行。
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;

CREATE POLICY users_self_select ON users FOR SELECT
    USING (id = NULLIF(current_setting('app.current_user_id', true), '')::uuid);

CREATE POLICY users_self_update ON users FOR UPDATE
    USING (id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (id = NULLIF(current_setting('app.current_user_id', true), '')::uuid);

-- ---------- 应用运行时角色（RLS 生效的前提） ----------
-- 注意：docker-entrypoint 创建的 POSTGRES_USER（eduagent）是超级用户，
-- 超级用户无条件绕过 RLS。因此业务请求必须切到非超级用户角色：
--   连接后执行（同一事务/会话内）：
--     SET ROLE eduagent_app;
--     SELECT set_config('app.current_user_id', '<用户UUID>', true);
-- 或者由 DBA 为其设置登录密码后直接作为应用账号连接：
--     ALTER ROLE eduagent_app LOGIN PASSWORD '<强密码>';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eduagent_app') THEN
        CREATE ROLE eduagent_app NOSUPERUSER NOINHERIT NOLOGIN;
    END IF;
END $$;

GRANT eduagent_app TO eduagent;    -- 允许 eduagent 执行 SET ROLE eduagent_app
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO eduagent_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO eduagent_app;

-- ============================================================================
-- 初始化完成
-- ============================================================================

-- ============================================================================
-- 9. 编排层专用表（Agent Loop 审计 / 子 Agent 注册表 / Skill 注册表）
-- ----------------------------------------------------------------------------
-- 对应《编排层设计》第六章 DDL 增量；幂等风格与上文一致（IF NOT EXISTS）。
-- RLS：含 user_id 的表（agent_runs / guardrail_decisions）启用行级安全，
-- 策略与第 8 节 14 表一致（app.current_user_id GUC）；
-- context_compactions / tool_call_audits 经父表归属判定（同 messages/kb_chunks）；
-- sub_agent_definitions / skill_definitions 为全局只读注册表，不启用 RLS。
-- ============================================================================

-- Agent Loop 运行记录
CREATE TABLE IF NOT EXISTS agent_runs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
    user_id      UUID REFERENCES users(id) ON DELETE CASCADE,
    turn_count   INTEGER NOT NULL DEFAULT 0,
    model_route  VARCHAR(50),                       -- 推理网关模型路由
    status       VARCHAR(20),                       -- running/completed/failed/circuit_broken
    token_usage  JSONB,                             -- {input, output, by_turn: []}
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMP
);

-- 工具调用审计
CREATE TABLE IF NOT EXISTS tool_call_audits (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      UUID REFERENCES agent_runs(id) ON DELETE CASCADE,
    tool_name   VARCHAR(50),
    input       JSONB,
    decision    VARCHAR(10),                        -- allow/ask/deny（护栏链结果）
    latency_ms  INTEGER,
    result_size INTEGER,
    card_type   VARCHAR(30),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 子 Agent 注册表（全局只读；YAML 内置基线为源，DB 支持运行时增改）
CREATE TABLE IF NOT EXISTS sub_agent_definitions (
    name            VARCHAR(50) PRIMARY KEY,
    description     TEXT,
    subject_routing JSONB,                          -- {math: "edu-math-7b", default: "edu-general-14b"}
    tools_allowlist TEXT[],
    async_via       VARCHAR(10),                    -- none/celery
    timeout_s       INTEGER NOT NULL DEFAULT 30,
    card_type       VARCHAR(30),
    input_form      JSONB,
    version         INTEGER NOT NULL DEFAULT 1,
    is_active       BOOLEAN NOT NULL DEFAULT true
);

-- Skill 注册表（工具栏，全局只读）
CREATE TABLE IF NOT EXISTS skill_definitions (
    code              VARCHAR(30) PRIMARY KEY,      -- photo_solve/grading/doc_parse/...
    name              VARCHAR(50),
    icon              VARCHAR(10),
    roles             TEXT[],                       -- {student,teacher,parent}
    prompt_template   TEXT,
    input_form_schema JSONB,
    card_type         VARCHAR(30),
    sort_order        INTEGER,
    is_active         BOOLEAN NOT NULL DEFAULT true
);

-- 上下文压缩审计（四级管线每触发一次一行）
CREATE TABLE IF NOT EXISTS context_compactions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
    level               VARCHAR(20),                -- slim/microcompact/autocompact/reactive
    tokens_before       INTEGER,
    tokens_after        INTEGER,
    summary             TEXT,
    boundary_message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 护栏决策审计（每次工具调用的每一环节一行）
CREATE TABLE IF NOT EXISTS guardrail_decisions (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID REFERENCES users(id) ON DELETE CASCADE,
    run_id     UUID REFERENCES agent_runs(id) ON DELETE CASCADE,
    tool_name  VARCHAR(50),
    stage      VARCHAR(20),                         -- moderation/role/quota/parental
    decision   VARCHAR(10),                         -- allow/ask/deny
    reason     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 编排层索引 ----------
CREATE INDEX IF NOT EXISTS idx_agent_runs_session           ON agent_runs(session_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_user_created      ON agent_runs(user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_tool_audits_run              ON tool_call_audits(run_id);
CREATE INDEX IF NOT EXISTS idx_context_compactions_session  ON context_compactions(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_guardrail_decisions_user     ON guardrail_decisions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_guardrail_decisions_run      ON guardrail_decisions(run_id);

-- ---------- 编排层 RLS ----------
-- 直接含 user_id 的表：本人可见可写（策略与第 8 节 14 表相同写法）
DO $$
DECLARE
    t TEXT;
    orchestration_user_tables TEXT[] := ARRAY['agent_runs', 'guardrail_decisions'];
BEGIN
    FOREACH t IN ARRAY orchestration_user_tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',  t);
        EXECUTE format(
            'CREATE POLICY %I ON %I FOR ALL
             USING (user_id = NULLIF(current_setting(''app.current_user_id'', true), '''')::uuid)
             WITH CHECK (user_id = NULLIF(current_setting(''app.current_user_id'', true), '''')::uuid)',
            t || '_owner_isolation', t);
    END LOOP;
END $$;

-- context_compactions：通过所属 session 的 user_id 判定归属（同 messages）
ALTER TABLE context_compactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE context_compactions FORCE ROW LEVEL SECURITY;

CREATE POLICY context_compactions_owner_isolation ON context_compactions FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM chat_sessions s
            WHERE s.id = context_compactions.session_id
              AND s.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM chat_sessions s
            WHERE s.id = context_compactions.session_id
              AND s.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

-- tool_call_audits：通过所属 run 的 user_id 判定归属（同 kb_chunks 经父表）
ALTER TABLE tool_call_audits ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_call_audits FORCE ROW LEVEL SECURITY;

CREATE POLICY tool_call_audits_owner_isolation ON tool_call_audits FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM agent_runs r
            WHERE r.id = tool_call_audits.run_id
              AND r.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM agent_runs r
            WHERE r.id = tool_call_audits.run_id
              AND r.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

-- ---------- 编排层表授权（沿用第 8 节应用角色） ----------
GRANT SELECT, INSERT, UPDATE, DELETE ON
    agent_runs, tool_call_audits, context_compactions, guardrail_decisions
    TO eduagent_app;
GRANT SELECT ON sub_agent_definitions, skill_definitions TO eduagent_app;

-- ---------- 注册表种子数据（与 app/subagents/definitions/*.yaml 一致） ----------
INSERT INTO sub_agent_definitions
    (name, description, subject_routing, tools_allowlist, async_via, timeout_s, card_type, input_form)
VALUES
    ('grading_agent', '作业批改：圈错、正解、知识点归因',
     '{"math": "edu-math-7b", "default": "edu-general-14b"}',
     '{ocr_extract, calculator, equation_solver, mistake_recorder}',
     'celery', 30, 'grading',
     '[{"field": "subject", "type": "select", "required": true}]'),
    ('ppt_agent', '制作 PPT 课件：大纲生成与排版渲染',
     '{"default": "edu-general-14b"}',
     '{web_search, knowledge_graph_query}',
     'celery', 30, 'ppt_preview',
     '[{"field": "topic", "type": "text", "required": true},
       {"field": "grade", "type": "select", "required": true},
       {"field": "subject", "type": "select", "required": true},
       {"field": "pages", "type": "number", "required": false}]'),
    ('lesson_plan_agent', '制作教案：教学目标、重难点与教学过程设计',
     '{"default": "edu-general-14b"}',
     '{web_search, knowledge_graph_query}',
     'celery', 30, 'lesson_plan_preview',
     '[{"field": "course_name", "type": "text", "required": true},
       {"field": "grade", "type": "select", "required": true},
       {"field": "duration", "type": "number", "required": false}]'),
    ('exam_paper_agent', '生成试卷：按题型/难度/知识点组卷并校验质量',
     '{"math": "edu-math-7b", "default": "edu-general-14b"}',
     '{knowledge_graph_query, calculator}',
     'celery', 30, 'exam_preview',
     '[{"field": "grade", "type": "select", "required": true},
       {"field": "subject", "type": "select", "required": true},
       {"field": "question_types", "type": "multiselect", "required": true},
       {"field": "difficulty", "type": "select", "required": false},
       {"field": "count", "type": "number", "required": false}]'),
    ('learning_analysis_agent', '学情分析：班级/学生维度的错题统计与教学建议报告',
     '{"default": "edu-general-14b"}',
     '{knowledge_graph_query}',
     'celery', 30, 'analysis_report',
     '[{"field": "class_name", "type": "text", "required": true},
       {"field": "analysis_type", "type": "select", "required": false}]'),
    ('oral_scoring_agent', '口语评分：发音/流利度/完整度多维度打分与纠音建议',
     '{"default": "edu-general-14b"}',
     '{}',
     'none', 20, 'oral_score',
     '[{"field": "transcript", "type": "text", "required": true},
       {"field": "scenario", "type": "select", "required": false}]')
ON CONFLICT (name) DO NOTHING;

-- ---------- Skill 种子数据（与 app/skills/builtin_skills.yaml 一致） ----------
INSERT INTO skill_definitions
    (code, name, icon, roles, prompt_template, input_form_schema, card_type, sort_order, is_active)
VALUES
    ('photo_solve', '拍照答疑', '📷', '{student, parent}',
     '请解答这张图片中的题目。先 OCR 识别题目内容，再分步讲解解题思路，最后给出答案与易错点提醒。{params}',
     '[{"field": "image_url", "type": "image", "required": true, "label": "题目照片"},
       {"field": "subject", "type": "select", "required": false, "label": "学科"}]',
     NULL, 1, true),
    ('grading', '作业批改', '✅', '{student, parent}',
     '请批改这份作业：逐题判断对错，圈出错误并给出正确答案，归因到知识点，把典型错题记录到错题本。{params}',
     '[{"field": "image_url", "type": "image", "required": true, "label": "作业照片"},
       {"field": "subject", "type": "select", "required": true, "label": "学科"}]',
     'grading', 2, true),
    ('doc_parse', '文档解析', '📄', '{student, teacher, parent}',
     '请解析这份文档，提取核心内容与结构，用要点形式总结，并回答我的追问。{params}',
     '[{"field": "file_url", "type": "file", "required": true, "label": "文档文件"}]',
     NULL, 3, true),
    ('oral_practice', '口语对练', '🎤', '{student, parent}',
     '请和我进行英语口语对练。根据我的表达从发音、流利度、完整度评分，给出纠音建议，并继续下一句对话。{params}',
     '[{"field": "scenario", "type": "select", "required": false, "label": "练习场景"},
       {"field": "transcript", "type": "text", "required": false, "label": "我的表达"}]',
     'oral_score', 4, true),
    ('formula_helper', '公式助手', '∑', '{student, teacher}',
     '请帮助处理数学公式：化简/求值/解方程，展示关键步骤，必要时用计算器工具验证结果。{params}',
     '[{"field": "expression", "type": "text", "required": true, "label": "公式或方程"}]',
     NULL, 5, true),
    ('chemistry_equation', '化学方程式', '⚗️', '{student, teacher}',
     '请配平并讲解这个化学反应方程式，说明反应类型、条件与现象。{params}',
     '[{"field": "equation", "type": "text", "required": true, "label": "化学方程式"}]',
     NULL, 6, true),
    ('knowledge_check', '知识点检测', '🎯', '{student, parent}',
     '请围绕指定知识点出 3-5 道检测题，逐题等我作答后批改，并给出掌握度评估与巩固建议。{params}',
     '[{"field": "concept", "type": "text", "required": true, "label": "知识点"},
       {"field": "subject", "type": "select", "required": false, "label": "学科"}]',
     NULL, 7, true),
    ('study_plan', '学习计划', '📅', '{student, parent}',
     '请根据我的目标与当前水平制定一份可执行的每日学习计划，任务具体到天、时长合理。{params}',
     '[{"field": "goal_description", "type": "text", "required": true, "label": "学习目标"},
       {"field": "current_level", "type": "text", "required": true, "label": "当前水平"},
       {"field": "days", "type": "number", "required": true, "label": "计划天数"}]',
     'plan_card', 8, true),
    ('mistake_book', '错题本', '📕', '{student, parent}',
     '请查看我的错题本：优先安排到期的复习题，逐题带我复习并记录复习结果；也可以把新错题录入错题本。{params}',
     '[{"field": "subject", "type": "select", "required": false, "label": "学科"}]',
     'mistake_card', 9, true),
    ('flashcards', '记忆卡片', '🃏', '{student, parent}',
     '请把给定内容做成一组记忆卡片（正面问题、背面答案），生成后带我按间隔重复节奏复习。{params}',
     '[{"field": "content", "type": "text", "required": false, "label": "制卡内容"},
       {"field": "count", "type": "number", "required": false, "label": "卡片数量"}]',
     'flashcard_group', 10, true),
    ('essay_coach', '作文辅导', '✏️', '{student}',
     '请辅导我的作文：先点评结构与立意，再逐段给出修改建议与示范改写，最后总结可复用的写作技巧。{params}',
     '[{"field": "essay", "type": "text", "required": true, "label": "作文内容"},
       {"field": "grade", "type": "select", "required": false, "label": "年级"}]',
     NULL, 11, true),
    ('web_search', '联网搜索', '🔍', '{student, teacher}',
     '请联网检索与问题相关的权威教育资料，综合后作答，并附上来源链接。{params}',
     '[{"field": "query", "type": "text", "required": true, "label": "搜索内容"}]',
     NULL, 12, true),
    ('make_ppt', '制作 PPT', '📊', '{teacher}',
     '请为课程制作 PPT 课件：设计每页大纲与要点，风格简洁适合课堂投屏。{params}',
     '[{"field": "topic", "type": "text", "required": true, "label": "课件主题"},
       {"field": "grade", "type": "select", "required": true, "label": "年级"},
       {"field": "subject", "type": "select", "required": true, "label": "学科"},
       {"field": "pages", "type": "number", "required": false, "label": "页数"}]',
     'ppt_preview', 13, true),
    ('make_lesson_plan', '制作教案', '📋', '{teacher}',
     '请撰写一份完整教案：教学目标、重难点、分阶段教学过程、板书设计与作业布置。{params}',
     '[{"field": "course_name", "type": "text", "required": true, "label": "课程名称"},
       {"field": "grade", "type": "select", "required": true, "label": "年级"},
       {"field": "duration", "type": "number", "required": false, "label": "课时分钟数"}]',
     'lesson_plan_preview', 14, true),
    ('make_exam_paper', '生成试卷', '📝', '{teacher}',
     '请按题型与难度要求组一份试卷，覆盖指定知识点，含答案与解析，题目不重复。{params}',
     '[{"field": "grade", "type": "select", "required": true, "label": "年级"},
       {"field": "subject", "type": "select", "required": true, "label": "学科"},
       {"field": "question_types", "type": "multiselect", "required": true, "label": "题型"},
       {"field": "difficulty", "type": "select", "required": false, "label": "难度"},
       {"field": "count", "type": "number", "required": false, "label": "题量"}]',
     'exam_preview', 15, true),
    ('learning_analysis', '学情分析', '📈', '{teacher}',
     '请基于班级错题与成绩数据做学情分析：输出知识点掌握热力、薄弱点与教学建议报告。{params}',
     '[{"field": "class_name", "type": "text", "required": true, "label": "班级名称"},
       {"field": "analysis_type", "type": "select", "required": false, "label": "分析类型"}]',
     'analysis_report', 16, true)
ON CONFLICT (code) DO NOTHING;
