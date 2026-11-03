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
