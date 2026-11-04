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
    email             VARCHAR(100),                     -- 邮箱（可选）
    grade_level       VARCHAR(20),                      -- 学段：primary/junior/senior/university
    default_subject   VARCHAR(20),                      -- 默认学科
    is_teacher        BOOLEAN NOT NULL DEFAULT false,   -- 是否教师身份
    is_paid           BOOLEAN NOT NULL DEFAULT false,   -- 是否付费用户（限流分层用）
    user_mode         VARCHAR(20) NOT NULL DEFAULT 'student',  -- 模式：student/teacher/parent
    teacher_verified_at TIMESTAMP,                      -- 教师资质认证通过时间
    created_at        TIMESTAMP NOT NULL DEFAULT now()
);

-- 游客登录 / 第三方登录扩展列（幂等：老库重跑本脚本亦可补齐）
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_guest      BOOLEAN NOT NULL DEFAULT false;  -- 游客账号（设备 ID 签发）
ALTER TABLE users ADD COLUMN IF NOT EXISTS device_id     VARCHAR(64);                     -- 游客绑定设备
ALTER TABLE users ADD COLUMN IF NOT EXISTS wechat_openid VARCHAR(64);                     -- 微信 openid
ALTER TABLE users ADD COLUMN IF NOT EXISTS apple_sub     VARCHAR(255);                    -- Apple 用户标识 sub
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_wechat_openid ON users (wechat_openid) WHERE wechat_openid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_apple_sub     ON users (apple_sub)     WHERE apple_sub IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_device_id           ON users (device_id)     WHERE device_id IS NOT NULL;

-- 用户自带大模型 API Key（AES-256-GCM 加密存储，永不回显明文）
CREATE TABLE IF NOT EXISTS user_api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider        VARCHAR(20) NOT NULL,               -- openai/anthropic/qwen/...
    encrypted_key   TEXT NOT NULL,                      -- AES-256-GCM 密文（base64）
    monthly_budget  INTEGER,                            -- 月度额度（单位：千 tokens，NULL=不限）
    used_budget     INTEGER NOT NULL DEFAULT 0,         -- 当月已用额度
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMP NOT NULL DEFAULT now()
);

-- ============================================================================
-- 2. 聊天与消息
-- ============================================================================

-- 聊天会话
CREATE TABLE IF NOT EXISTS chat_sessions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title             VARCHAR(200),                     -- 会话标题（首条消息自动生成）
    subject           VARCHAR(20),                      -- 学科
    model             VARCHAR(50),                      -- 使用的模型标识
    inference_source  VARCHAR(20),                      -- 推理来源：internal（自有 vLLM）/ external（用户 Key）
    created_at        TIMESTAMP NOT NULL DEFAULT now(),
    updated_at        TIMESTAMP NOT NULL DEFAULT now()
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
    created_at      TIMESTAMP NOT NULL DEFAULT now()
);

-- ============================================================================
-- 3. 学习数据（错题 / 记忆卡片 / 学习计划）
-- ============================================================================

-- 错题本
CREATE TABLE IF NOT EXISTS mistake_records (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject           VARCHAR(20) NOT NULL,
    question_text     TEXT,                             -- 题目文本（OCR/手动录入）
    question_image_url TEXT,                            -- 题目图片（MinIO 预签名 URL 或对象 key）
    user_answer       TEXT,                             -- 用户作答
    correct_answer    TEXT,                             -- 正确答案
    analysis          TEXT,                             -- AI 解析
    knowledge_tags    TEXT[],                           -- 知识点标签
    source_message_id UUID REFERENCES messages(id) ON DELETE SET NULL, -- 来源消息（拍照答疑链路）
    review_count      INTEGER NOT NULL DEFAULT 0,       -- 已复习次数
    next_review_at    TIMESTAMP,                        -- 下次复习时间（艾宾浩斯曲线驱动）
    created_at        TIMESTAMP NOT NULL DEFAULT now()
);

-- 记忆卡片（SM-2 间隔重复参数）
CREATE TABLE IF NOT EXISTS flashcards (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject         VARCHAR(20) NOT NULL,
    front_content   TEXT NOT NULL,                      -- 正面（问题）
    back_content    TEXT NOT NULL,                      -- 背面（答案）
    knowledge_tags  TEXT[],
    interval        INTEGER NOT NULL DEFAULT 1,         -- 当前间隔（天）
    ease_factor     FLOAT NOT NULL DEFAULT 2.5,         -- 难度系数
    due_date        DATE,                               -- 到期日
    created_at      TIMESTAMP NOT NULL DEFAULT now()
);

-- 学习计划
CREATE TABLE IF NOT EXISTS study_plans (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title               VARCHAR(200) NOT NULL,
    goal_description    TEXT,                           -- 目标描述
    start_date          DATE,
    end_date            DATE,
    status              VARCHAR(20) NOT NULL DEFAULT 'active', -- active/completed/archived
    generated_schedule  JSONB,                          -- AI 生成的日程安排
    created_at          TIMESTAMP NOT NULL DEFAULT now()
);

-- ============================================================================
-- 4. 知识库（RAG）
-- ============================================================================

-- 知识库文档（用户上传的资料）
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       VARCHAR(200) NOT NULL,
    file_url    TEXT NOT NULL,                          -- MinIO 对象地址
    file_type   VARCHAR(10),                            -- pdf/docx/txt/md/...
    status      VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending/processing/ready/failed
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

-- 文档切片 + 向量（embedding 维度 1536，与所选 embedding 模型对齐）
CREATE TABLE IF NOT EXISTS kb_chunks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id  UUID NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    chunk_text   TEXT NOT NULL,
    embedding    VECTOR(1536),
    page_number  INTEGER,                               -- 原文页码（用于引用定位）
    metadata     JSONB                                  -- 章节标题、段落序号等
);

-- ============================================================================
-- 5. 长期记忆
-- ============================================================================

CREATE TABLE IF NOT EXISTS memories (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    memory_type       VARCHAR(20) NOT NULL,             -- profile/preference/weak_point/event
    content           TEXT NOT NULL,
    embedding         VECTOR(1536),
    source_session_id UUID,                             -- 来源会话（不建 FK，允许会话删除后记忆留存）
    created_at        TIMESTAMP NOT NULL DEFAULT now()
);

-- ============================================================================
-- 6. 内容生成产物（教案 / PPT / 试卷 / 学情分析）
-- ============================================================================

-- 教案
CREATE TABLE IF NOT EXISTS lesson_plans (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title         VARCHAR(200) NOT NULL,
    course_name   VARCHAR(100),                         -- 课程名称
    grade         VARCHAR(20),
    subject       VARCHAR(20),
    duration      INTEGER,                              -- 课时长度（分钟）
    template_type VARCHAR(20),                          -- 模板类型
    content       JSONB,                                -- 教案结构化内容
    file_url      TEXT,                                 -- 生成的 docx/pdf 文件地址
    status        VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending/generating/completed/failed
    created_at    TIMESTAMP NOT NULL DEFAULT now()
);

-- PPT 演示文稿
CREATE TABLE IF NOT EXISTS presentations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           VARCHAR(200) NOT NULL,
    topic           VARCHAR(200),
    grade           VARCHAR(20),
    subject         VARCHAR(20),
    style           VARCHAR(20),                        -- 模板风格
    slides          JSONB,                              -- 幻灯片结构化内容（含每页大纲/正文/版式）
    file_url        TEXT,                               -- 生成的 pptx 文件地址
    thumbnail_urls  TEXT[],                             -- 各页缩略图
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMP NOT NULL DEFAULT now()
);

-- 试卷
CREATE TABLE IF NOT EXISTS exam_papers (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title             VARCHAR(200) NOT NULL,
    grade             VARCHAR(20),
    subject           VARCHAR(20),
    difficulty        VARCHAR(20),                      -- easy/medium/hard/mixed
    questions         JSONB,                            -- 题目结构化内容（题型/题干/选项/答案/解析）
    student_file_url  TEXT,                             -- 学生版（无答案）
    teacher_file_url  TEXT,                             -- 教师版（含答案解析）
    status            VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at        TIMESTAMP NOT NULL DEFAULT now()
);

-- 学情分析（教师端）
CREATE TABLE IF NOT EXISTS learning_analyses (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE, -- 发起分析的教师
    class_name        VARCHAR(100),
    analysis_type     VARCHAR(20),                      -- class_overview/student_detail/error_trend/...
    target_student_id UUID REFERENCES users(id) ON DELETE SET NULL,  -- 可空：针对单个学生的分析
    data_source       JSONB,                            -- 输入数据来源说明（范围、时间窗等）
    report_content    JSONB,                            -- 报告结构化内容
    file_url          TEXT,                             -- 导出的报告文件
    created_at        TIMESTAMP NOT NULL DEFAULT now()
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
CREATE INDEX IF NOT EXISTS idx_mistake_records_knowledge_tags  ON mistake_records USING GIN(knowledge_tags);
CREATE INDEX IF NOT EXISTS idx_flashcards_user_due             ON flashcards(user_id, due_date);
CREATE INDEX IF NOT EXISTS idx_flashcards_knowledge_tags       ON flashcards USING GIN(knowledge_tags);
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
CREATE INDEX IF NOT EXISTS idx_learning_analyses_target_student ON learning_analyses(target_student_id);

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
    started_at   TIMESTAMP NOT NULL DEFAULT now(),
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
    created_at  TIMESTAMP NOT NULL DEFAULT now()
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
    created_at          TIMESTAMP NOT NULL DEFAULT now()
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
    created_at TIMESTAMP NOT NULL DEFAULT now()
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

-- ============================================================================
-- 10. 增强机制扩展包专用表（Wave C 交互协作机制）
-- ----------------------------------------------------------------------------
-- 对应《增强机制设计》§七 DDL 增量（本批仅 plan_documents / stop_hook_audits
-- 两表 + agent_runs.todo_snapshot 列；幂等风格与第 9 节一致）。
-- RLS：plan_documents 含 user_id → 本人可见可写（同 agent_runs 写法）；
-- stop_hook_audits 经 agent_runs 归属判定（同 tool_call_audits 写法）。
-- ============================================================================

-- Plan Mode 大纲文档（两段式生成：drafting→awaiting_confirm→confirmed→generating→completed）
CREATE TABLE IF NOT EXISTS plan_documents (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id         UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
    user_id            UUID REFERENCES users(id) ON DELETE CASCADE,
    skill_code         VARCHAR(30),                     -- ppt/lesson_plan/exam_paper/study_plan/analysis
    version            INTEGER NOT NULL DEFAULT 1,
    outline            JSONB NOT NULL,
    status             VARCHAR(20),                     -- drafting/awaiting_confirm/confirmed/generating/completed/cancelled/expired
    confirm_token_hash VARCHAR(64),                     -- 仅存 SHA-256 哈希，禁止存原文
    confirm_expires_at TIMESTAMP,                       -- 30 分钟过期
    parent_plan_id     UUID REFERENCES plan_documents(id) ON DELETE SET NULL,  -- 修改产生的新版本指回旧版本
    created_at         TIMESTAMP NOT NULL DEFAULT now(),
    confirmed_at       TIMESTAMP
);

-- Stop Hooks 校验审计（交付前质量闸口）
CREATE TABLE IF NOT EXISTS stop_hook_audits (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                UUID REFERENCES agent_runs(id) ON DELETE CASCADE,
    hook_name             VARCHAR(50),                  -- math_verify/grading_consistency/completeness_check
    passed                BOOLEAN,
    detail                JSONB,                        -- 失败原因、复核数据
    forced_continue_count INTEGER,                      -- 本次为第几次强制继续
    created_at            TIMESTAMP NOT NULL DEFAULT now()
);

-- AgentTodo 快照列（agent_runs 已部署，幂等补列）
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS todo_snapshot JSONB;

-- ---------- 增强机制索引 ----------
CREATE INDEX IF NOT EXISTS idx_plan_user           ON plan_documents(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_plan_session        ON plan_documents(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_stop_hook_audits_run ON stop_hook_audits(run_id);

-- ---------- 增强机制 RLS ----------
-- plan_documents：直接含 user_id（策略与 agent_runs 相同写法）
ALTER TABLE plan_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE plan_documents FORCE ROW LEVEL SECURITY;

CREATE POLICY plan_documents_owner_isolation ON plan_documents FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid);

-- stop_hook_audits：通过所属 run 的 user_id 判定归属（同 tool_call_audits）
ALTER TABLE stop_hook_audits ENABLE ROW LEVEL SECURITY;
ALTER TABLE stop_hook_audits FORCE ROW LEVEL SECURITY;

CREATE POLICY stop_hook_audits_owner_isolation ON stop_hook_audits FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM agent_runs r
            WHERE r.id = stop_hook_audits.run_id
              AND r.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM agent_runs r
            WHERE r.id = stop_hook_audits.run_id
              AND r.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

-- ---------- 增强机制表授权（沿用第 8 节应用角色） ----------
GRANT SELECT, INSERT, UPDATE, DELETE ON plan_documents, stop_hook_audits TO eduagent_app;

-- ============================================================================
-- 11. 增强机制扩展包专用表（Wave D1：上下文与成本 + 智能增值）
-- ----------------------------------------------------------------------------
-- 对应《增强机制设计》§七 DDL 增量（本批：denial_counters / cost_ledger /
-- suggestion_logs / model_prices；幂等风格与第 9/10 节一致）。
-- RLS：含 user_id 的三表启用行级安全（策略同 plan_documents 写法）；
-- model_prices 为全局只读配置表，不启用 RLS（应用角色只授权 SELECT）。
-- ============================================================================

-- 3.3 Denial Tracking：拒绝计数（只影响推荐排序与默认值，禁止限制主动发起）
CREATE TABLE IF NOT EXISTS denial_counters (
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_code VARCHAR(50) NOT NULL,               -- Skill/工具/追问推荐类别
    consecutive INTEGER NOT NULL DEFAULT 0,         -- 连续拒绝次数（主动使用清零）
    total       INTEGER NOT NULL DEFAULT 0,         -- 累计拒绝次数（≥20 默认关闭建议）
    updated_at  TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, target_code)
);

-- 3.4 Cost Tracker：逐 run 成本台账（单价快照随行落库，对账可溯源）
CREATE TABLE IF NOT EXISTS cost_ledger (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              UUID REFERENCES agent_runs(id) ON DELETE CASCADE,
    user_id             UUID REFERENCES users(id) ON DELETE CASCADE,
    model_route         VARCHAR(50),
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    cache_hit_tokens    INTEGER NOT NULL DEFAULT 0,
    unit_price_snapshot JSONB,                      -- {input_per_1k, output_per_1k, currency}；无单价记录时 NULL（只记 tokens）
    cost_cents          INTEGER,                    -- 折算成本（美分）；未计价 NULL
    quota_tier          VARCHAR(20),                -- 身份档快照（free/paid/teacher）
    created_at          TIMESTAMP NOT NULL DEFAULT now()
);

-- 3.4 单价配置表：按 provider+model 配置，effective_from 支持历史快照
CREATE TABLE IF NOT EXISTS model_prices (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider            VARCHAR(30) NOT NULL,
    model               VARCHAR(50) NOT NULL,
    input_per_1k_cents  INTEGER NOT NULL,           -- 输入单价（美分/1k tokens）
    output_per_1k_cents INTEGER NOT NULL,           -- 输出单价（美分/1k tokens）
    currency            VARCHAR(8) NOT NULL DEFAULT 'USD',
    effective_from      TIMESTAMP NOT NULL DEFAULT now(),
    is_active           BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT uq_model_prices_route_effective UNIQUE (provider, model, effective_from)
);

-- 4.1 PromptSuggestion：追问推荐日志（clicked_index 未点为 NULL）
CREATE TABLE IF NOT EXISTS suggestion_logs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
    user_id       UUID REFERENCES users(id) ON DELETE CASCADE,
    items         JSONB,                            -- 3 条候选（已过内容审核）
    clicked_index INTEGER,                          -- 用户点了哪条，未点为 NULL
    created_at    TIMESTAMP NOT NULL DEFAULT now()
);

-- ---------- Wave D1 索引 ----------
CREATE INDEX IF NOT EXISTS idx_cost_ledger_user_month ON cost_ledger(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_cost_ledger_run        ON cost_ledger(run_id);
CREATE INDEX IF NOT EXISTS idx_suggestion_logs_user   ON suggestion_logs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_suggestion_logs_session ON suggestion_logs(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_prices_route     ON model_prices(provider, model, effective_from DESC);

-- ---------- Wave D1 RLS ----------
-- denial_counters / cost_ledger / suggestion_logs：直接含 user_id（同 plan_documents 写法）
ALTER TABLE denial_counters ENABLE ROW LEVEL SECURITY;
ALTER TABLE denial_counters FORCE ROW LEVEL SECURITY;

CREATE POLICY denial_counters_owner_isolation ON denial_counters FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid);

ALTER TABLE cost_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE cost_ledger FORCE ROW LEVEL SECURITY;

CREATE POLICY cost_ledger_owner_isolation ON cost_ledger FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid);

ALTER TABLE suggestion_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE suggestion_logs FORCE ROW LEVEL SECURITY;

CREATE POLICY suggestion_logs_owner_isolation ON suggestion_logs FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid);

-- model_prices：全局只读配置表，不启用 RLS（单价由运维/迁移维护，应用只读）

-- ---------- Wave D1 表授权（沿用第 8 节应用角色） ----------
GRANT SELECT, INSERT, UPDATE, DELETE ON denial_counters, cost_ledger, suggestion_logs TO eduagent_app;
GRANT SELECT ON model_prices TO eduagent_app;

-- ============================================================================
-- 12. 增强机制扩展包专用表（Wave D2：DreamTask + 工程基础设施）
-- ----------------------------------------------------------------------------
-- 对应《增强机制设计》§七 DDL 增量（本批：dream_runs / feature_gates /
-- output_styles / automation_hooks；幂等风格与第 9/10/11 节一致）。
-- RLS：dream_runs（user_id）与 automation_hooks（owner_id）启用行级安全；
-- feature_gates / output_styles 为全局只读配置表，不启用 RLS（应用只读，
-- 增改由运维/运营后台迁移通道执行）。
-- ============================================================================

-- 4.2 DreamTask：夜间记忆整合运行记录（每用户每日最多一条 completed）
CREATE TABLE IF NOT EXISTS dream_runs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID REFERENCES users(id) ON DELETE CASCADE,
    phase        VARCHAR(20),               -- orient/gather/consolidate/prune/completed/failed
    stats        JSONB,                     -- {mistakes_organized, cards_created, memories_updated, ...}
    retry_count  INTEGER NOT NULL DEFAULT 0,
    started_at   TIMESTAMP NOT NULL DEFAULT now(),
    finished_at  TIMESTAMP,
    delivered_at TIMESTAMP                  -- dream.summary 摘要已推送时间（NULL=待次日首启推送）
);

-- 5.2 Feature Gates：灰度注册表（新机制默认 enabled=false / rollout_pct=0）
CREATE TABLE IF NOT EXISTS feature_gates (
    key         VARCHAR(50) PRIMARY KEY,
    enabled     BOOLEAN NOT NULL DEFAULT false,
    rollout_pct INTEGER NOT NULL DEFAULT 0,  -- 0-100，user_id 哈希分桶
    allowlist   UUID[],                      -- 白名单优先于分桶
    updated_at  TIMESTAMP NOT NULL DEFAULT now()
);

-- 5.3 Output Styles：年级+学科 → 语气提示词（运营后台增改，进程内缓存 60s 热生效）
CREATE TABLE IF NOT EXISTS output_styles (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    grade_level VARCHAR(20) NOT NULL,        -- 精确年级（如 grade_3）；'*' 通配全部年级
    subject     VARCHAR(20) NOT NULL,        -- 学科编码；'*' 通配全部学科
    tone_prompt TEXT NOT NULL,               -- 只影响表述方式，禁止含绕护栏指令（上线前安全评审）
    is_active   BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT uq_output_styles_grade_subject UNIQUE (grade_level, subject)
);

-- 5.4 Automation Hooks：event → action 配置（全部异步执行，失败不影响主流程）
CREATE TABLE IF NOT EXISTS automation_hooks (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id   UUID REFERENCES users(id) ON DELETE CASCADE,  -- 配置者（家长/教师）
    event      VARCHAR(40) NOT NULL,         -- mistake.created/plan.confirmed/dream.completed
    action     VARCHAR(40) NOT NULL,         -- notify_parent/notify_teacher
    config     JSONB,                        -- 动作参数（如通知渠道/接收人覆盖）
    is_active  BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

-- 5.1 分段 Checkpoint：agent_runs 补 timings 列（每 run 各阶段毫秒数，JSONB）
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS timings JSONB;

-- ---------- Wave D2 索引 ----------
CREATE INDEX IF NOT EXISTS idx_dream_runs_user      ON dream_runs(user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_automation_hooks_evt ON automation_hooks(event) WHERE is_active;

-- ---------- Wave D2 种子行（output_styles 少量基线，幂等） ----------
INSERT INTO output_styles (grade_level, subject, tone_prompt) VALUES
    ('*',        '*',     '语气亲切鼓励，用词通俗，多举生活化例子。'),
    ('grade_1',  '*',     '面向低年级：句子短、用词简单，多用提问引导思考。'),
    ('grade_7',  'math',  '面向初中数学：讲解严谨有条理，关键步骤逐一展示。')
ON CONFLICT (grade_level, subject) DO NOTHING;

-- ---------- Wave D2 RLS ----------
-- dream_runs：直接含 user_id（同 plan_documents 写法）
ALTER TABLE dream_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE dream_runs FORCE ROW LEVEL SECURITY;

CREATE POLICY dream_runs_owner_isolation ON dream_runs FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid);

-- automation_hooks：按配置者 owner_id 隔离
ALTER TABLE automation_hooks ENABLE ROW LEVEL SECURITY;
ALTER TABLE automation_hooks FORCE ROW LEVEL SECURITY;

CREATE POLICY automation_hooks_owner_isolation ON automation_hooks FOR ALL
    USING (owner_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (owner_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid);

-- feature_gates / output_styles：全局只读配置表，不启用 RLS（应用角色只授权 SELECT）

-- ---------- Wave D2 表授权（沿用第 8 节应用角色） ----------
GRANT SELECT, INSERT, UPDATE, DELETE ON dream_runs, automation_hooks TO eduagent_app;
GRANT SELECT ON feature_gates, output_styles TO eduagent_app;

-- ============================================================================
-- 11. Billing v1：会员订阅与支付（指导/后端设计/计费/spec.md）
-- ----------------------------------------------------------------------------
-- 额度单位 = USD 成本美分（与 cost_ledger / pricing.py 一致）；
-- 套餐售价以 CNY 分展示，两者解耦。
-- ============================================================================

-- 11.1 套餐定义（全局只读配置表，种子数据维护，运营改套餐 = 改 SQL 重部署）
CREATE TABLE IF NOT EXISTS plans (
    id                   VARCHAR(30) PRIMARY KEY,     -- free / pro / teacher_pro
    name                 VARCHAR(50) NOT NULL,
    price_cents          INTEGER NOT NULL,            -- 月价（CNY 分，展示用）
    currency             VARCHAR(8) NOT NULL DEFAULT 'CNY',
    monthly_quota_cents  BIGINT NOT NULL,             -- 月额度（USD 成本美分）
    allowed_models       JSONB NOT NULL DEFAULT '[]', -- 空数组 = 全开放
    features             JSONB NOT NULL DEFAULT '{}', -- 功能开关
    sort_order           INTEGER NOT NULL DEFAULT 0,
    is_active            BOOLEAN NOT NULL DEFAULT true,
    created_at           TIMESTAMP NOT NULL DEFAULT now()
);

-- 11.2 订阅状态（一个用户可有多条历史，status=active 最多一条，由服务层守卫）
CREATE TABLE IF NOT EXISTS subscriptions (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id              VARCHAR(30) NOT NULL REFERENCES plans(id),
    status               VARCHAR(20) NOT NULL,        -- active / canceled / expired
    channel              VARCHAR(20) NOT NULL,        -- mock / apple_iap / google_play / stripe / alipay / wechat / admin
    channel_sub_id       VARCHAR(100),                -- 渠道侧订阅号（对账用）
    current_period_start TIMESTAMP NOT NULL,
    current_period_end   TIMESTAMP NOT NULL,
    auto_renew           BOOLEAN NOT NULL DEFAULT true,
    created_at           TIMESTAMP NOT NULL DEFAULT now(),
    updated_at           TIMESTAMP NOT NULL DEFAULT now()
);

-- 11.3 订单（channel_txn_id 唯一约束 = webhook 幂等键）
CREATE TABLE IF NOT EXISTS orders (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id         VARCHAR(30) NOT NULL REFERENCES plans(id),
    amount_cents    INTEGER NOT NULL,
    currency        VARCHAR(8) NOT NULL DEFAULT 'CNY',
    channel         VARCHAR(20) NOT NULL,
    channel_txn_id  VARCHAR(100) UNIQUE,              -- 渠道交易号，幂等键
    status          VARCHAR(20) NOT NULL,             -- pending / paid / refunded / failed
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    paid_at         TIMESTAMP
);

-- 11.4 额度周期账本（每月一行；不存在时按 free plan 惰性创建）
CREATE TABLE IF NOT EXISTS quota_periods (
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period_start DATE NOT NULL,                       -- 当月 1 号
    quota_cents  BIGINT NOT NULL,
    used_cents   BIGINT NOT NULL DEFAULT 0,
    created_at   TIMESTAMP NOT NULL DEFAULT now(),
    updated_at   TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, period_start)
);

-- ---------- Billing 索引 ----------
CREATE INDEX IF NOT EXISTS idx_subscriptions_user    ON subscriptions(user_id, status, current_period_end DESC);
CREATE INDEX IF NOT EXISTS idx_orders_user           ON orders(user_id, created_at DESC);

-- ---------- Billing 种子数据（三档，幂等） ----------
INSERT INTO plans (id, name, price_cents, monthly_quota_cents, allowed_models, features, sort_order) VALUES
    ('free',        '免费版',   0,    300,
     '["deepseek-chat", "deepseek-reasoner", "gpt-4o-mini", "gemini-2.5-flash", "gemini-1.5-flash", "claude-3-5-haiku", "kimi-k3"]',
     '{"rag_max_docs": 3, "docgen_per_month": 5}', 0),
    ('pro',         'Pro 会员', 6800, 3000,
     '[]',
     '{"rag_max_docs": 50, "docgen_per_month": 100}', 1),
    ('teacher_pro', '教师版',   9800, 5000,
     '[]',
     '{"rag_max_docs": 200, "docgen_per_month": 300, "teacher_tools": true}', 2)
ON CONFLICT (id) DO NOTHING;

-- ---------- Billing RLS ----------
ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscriptions FORCE ROW LEVEL SECURITY;

CREATE POLICY subscriptions_owner_isolation ON subscriptions FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid);

ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders FORCE ROW LEVEL SECURITY;

CREATE POLICY orders_owner_isolation ON orders FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid);

ALTER TABLE quota_periods ENABLE ROW LEVEL SECURITY;
ALTER TABLE quota_periods FORCE ROW LEVEL SECURITY;

CREATE POLICY quota_periods_owner_isolation ON quota_periods FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid);

-- plans：全局只读配置表，不启用 RLS（同 feature_gates / output_styles）

-- ---------- Billing 表授权（沿用第 8 节应用角色） ----------
GRANT SELECT, INSERT, UPDATE, DELETE ON subscriptions, orders, quota_periods TO eduagent_app;
GRANT SELECT ON plans TO eduagent_app;

-- BEGIN EDUAGENT ORM COLUMN COMPATIBILITY
-- Align the existing application mappings with the original bootstrap tables.
-- This is a compatibility repair, not a new feature/schema redesign: old columns,
-- data, indexes, policies and grants remain. Apply this file to an existing
-- database only as its migration owner; init.sql includes the identical block
-- for a fresh database. The single DO statement is atomic.
-- Backfill runs ONLY when a column is first added. Re-running must not replace
-- user edits, including intentional NULL, {} and [] values in existing columns.
-- Signed file_url values are preserved in their old columns, never inferred as
-- object keys. Legacy image-only mistakes retain a NULL question.
DO $orm_compat$
DECLARE
    item RECORD;
BEGIN
    FOR item IN
        SELECT * FROM (VALUES
        ('mistake_records', 'question', 'TEXT', 'question_text', NULL, false),
        ('mistake_records', 'wrong_answer', 'TEXT', 'user_answer', NULL, false),
        ('mistake_records', 'knowledge_points', 'JSONB', 'COALESCE(to_jsonb(knowledge_tags), ''[]''::jsonb)', '''[]''::jsonb', true),
        ('mistake_records', 'mastery', 'DOUBLE PRECISION', '0.0', '0.0', true),
        ('mistake_records', 'source', 'VARCHAR(50)', NULL, NULL, false),
        ('mistake_records', 'updated_at', 'TIMESTAMPTZ', 'created_at AT TIME ZONE ''UTC''', 'now()', true),
        ('flashcards', 'front', 'TEXT', 'front_content', NULL, true),
        ('flashcards', 'back', 'TEXT', 'back_content', NULL, true),
        ('flashcards', 'deck', 'VARCHAR(100)', '''default''', '''default''', true),
        ('flashcards', 'interval_days', 'INTEGER', '"interval"', '0', true),
        ('flashcards', 'repetitions', 'INTEGER', '0', '0', true),
        ('flashcards', 'next_review_at', 'TIMESTAMPTZ', 'due_date::timestamp AT TIME ZONE ''UTC''', NULL, false),
        ('flashcards', 'updated_at', 'TIMESTAMPTZ', 'created_at AT TIME ZONE ''UTC''', 'now()', true),
        ('study_plans', 'subject', 'VARCHAR(50)', NULL, NULL, false),
        ('study_plans', 'goal', 'TEXT', 'goal_description', NULL, false),
        ('study_plans', 'plan_content', 'JSONB', 'CASE WHEN jsonb_typeof(generated_schedule) = ''array'' THEN jsonb_build_object(''schedule'', generated_schedule) ELSE ''{}''::jsonb END', '''{}''::jsonb', true),
        ('study_plans', 'progress', 'DOUBLE PRECISION', '0.0', '0.0', true),
        ('study_plans', 'updated_at', 'TIMESTAMPTZ', 'created_at AT TIME ZONE ''UTC''', 'now()', true),
        ('knowledge_documents', 'file_name', 'VARCHAR(255)', NULL, NULL, false),
        ('knowledge_documents', 'object_key', 'VARCHAR(500)', NULL, NULL, false),
        ('knowledge_documents', 'size_bytes', 'BIGINT', NULL, NULL, false),
        ('knowledge_documents', 'subject', 'VARCHAR(50)', NULL, NULL, false),
        ('knowledge_documents', 'chunk_count', 'INTEGER', '(SELECT count(*) FROM public.kb_chunks c WHERE c.document_id = knowledge_documents.id)', '0', true),
        ('knowledge_documents', 'metadata', 'JSONB', '''{}''::jsonb', '''{}''::jsonb', true),
        ('knowledge_documents', 'updated_at', 'TIMESTAMPTZ', 'created_at AT TIME ZONE ''UTC''', 'now()', true),
        ('kb_chunks', 'user_id', 'UUID REFERENCES public.users(id) ON DELETE CASCADE', '(SELECT d.user_id FROM public.knowledge_documents d WHERE d.id = kb_chunks.document_id)', NULL, true),
        ('kb_chunks', 'chunk_index', 'INTEGER', NULL, NULL, true),
        ('kb_chunks', 'content', 'TEXT', 'chunk_text', NULL, true),
        ('kb_chunks', 'created_at', 'TIMESTAMPTZ', 'now()', 'now()', true),
        ('memories', 'importance', 'DOUBLE PRECISION', '0.5', '0.5', true),
        ('memories', 'metadata', 'JSONB', '''{}''::jsonb', '''{}''::jsonb', true),
        ('memories', 'updated_at', 'TIMESTAMPTZ', 'created_at AT TIME ZONE ''UTC''', 'now()', true),
        ('lesson_plans', 'grade_level', 'VARCHAR(50)', 'grade', NULL, false),
        ('lesson_plans', 'duration_minutes', 'INTEGER', 'duration', NULL, false),
        ('lesson_plans', 'objectives', 'JSONB', 'CASE WHEN jsonb_typeof(content->''objectives'') = ''array'' THEN content->''objectives'' ELSE ''[]''::jsonb END', '''[]''::jsonb', true),
        ('lesson_plans', 'updated_at', 'TIMESTAMPTZ', 'created_at AT TIME ZONE ''UTC''', 'now()', true),
        ('presentations', 'grade_level', 'VARCHAR(50)', 'grade', NULL, false),
        ('presentations', 'theme', 'VARCHAR(100)', 'style', NULL, false),
        ('presentations', 'slide_count', 'INTEGER', 'CASE WHEN jsonb_typeof(slides) = ''array'' THEN jsonb_array_length(slides) ELSE 0 END', '0', true),
        ('presentations', 'outline', 'JSONB', 'jsonb_build_object(''slides'', CASE WHEN jsonb_typeof(slides) = ''array'' THEN slides ELSE ''[]''::jsonb END, ''topic'', COALESCE(topic, title), ''_thumbnails'', COALESCE(to_jsonb(thumbnail_urls), ''[]''::jsonb))', '''[]''::jsonb', true),
        ('presentations', 'object_key', 'VARCHAR(500)', NULL, NULL, false),
        ('presentations', 'updated_at', 'TIMESTAMPTZ', 'created_at AT TIME ZONE ''UTC''', 'now()', true),
        ('exam_papers', 'grade_level', 'VARCHAR(50)', 'grade', NULL, false),
        ('exam_papers', 'total_score', 'INTEGER', NULL, NULL, false),
        ('exam_papers', 'duration_minutes', 'INTEGER', NULL, NULL, false),
        ('exam_papers', 'answer_key', 'JSONB', 'jsonb_strip_nulls(jsonb_build_object(''difficulty'', difficulty, ''student_file_url'', student_file_url, ''teacher_file_url'', teacher_file_url))', NULL, false),
        ('exam_papers', 'updated_at', 'TIMESTAMPTZ', 'created_at AT TIME ZONE ''UTC''', 'now()', true),
        ('learning_analyses', 'period_start', 'DATE', NULL, NULL, false),
        ('learning_analyses', 'period_end', 'DATE', NULL, NULL, false),
        ('learning_analyses', 'summary', 'TEXT', NULL, NULL, false),
        ('learning_analyses', 'metrics', 'JSONB', 'jsonb_strip_nulls(jsonb_build_object(''class_name'', class_name, ''data_source'', data_source, ''report'', report_content, ''file_url'', file_url))', '''{}''::jsonb', true),
        ('learning_analyses', 'suggestions', 'JSONB', '''[]''::jsonb', '''[]''::jsonb', true)
        ) AS columns_to_add(table_name, column_name, column_type, initial_expression, default_expression, require_value)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = item.table_name
              AND column_name = item.column_name
        ) THEN
            EXECUTE format('ALTER TABLE public.%I ADD COLUMN %I %s',
                           item.table_name, item.column_name, item.column_type);
            IF item.table_name = 'kb_chunks' AND item.column_name = 'chunk_index' THEN
                -- Original chunks have no timestamp/order column. UUID ordering
                -- is deterministic and keeps the existing text/vector rows intact.
                WITH numbered AS (
                    SELECT id, (row_number() OVER (PARTITION BY document_id ORDER BY id) - 1)::integer AS position
                    FROM public.kb_chunks
                )
                UPDATE public.kb_chunks AS chunk SET chunk_index = numbered.position
                FROM numbered WHERE chunk.id = numbered.id;
            ELSIF item.initial_expression IS NOT NULL THEN
                EXECUTE format('UPDATE public.%I SET %I = %s',
                               item.table_name, item.column_name, item.initial_expression);
            END IF;
            IF item.default_expression IS NOT NULL THEN
                EXECUTE format('ALTER TABLE public.%I ALTER COLUMN %I SET DEFAULT %s',
                               item.table_name, item.column_name, item.default_expression);
            END IF;
            IF item.require_value THEN
                EXECUTE format('ALTER TABLE public.%I ALTER COLUMN %I SET NOT NULL',
                               item.table_name, item.column_name);
            END IF;
        END IF;
    END LOOP;

    -- The current ORM writes their replacements; retain old values/columns,
    -- but let new rows omit the old-only mandatory inputs. Subject is optional
    -- in the existing MistakeRecord/Flashcard mappings and request schemas.
    ALTER TABLE public.mistake_records ALTER COLUMN subject DROP NOT NULL;
    ALTER TABLE public.flashcards ALTER COLUMN subject DROP NOT NULL;
    ALTER TABLE public.flashcards ALTER COLUMN front_content DROP NOT NULL;
    ALTER TABLE public.flashcards ALTER COLUMN back_content DROP NOT NULL;
    ALTER TABLE public.knowledge_documents ALTER COLUMN file_url DROP NOT NULL;
    ALTER TABLE public.kb_chunks ALTER COLUMN chunk_text DROP NOT NULL;
END
$orm_compat$;
-- END EDUAGENT ORM COLUMN COMPATIBILITY
