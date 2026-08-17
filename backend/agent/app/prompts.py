"""LLM Prompt 模板集中管理。

约定：
- 面向学生的分步讲解统一使用 <step>第n步：...</step> 标签，BFF/移动端按此解析渲染。
- 所有要求 LLM 输出 JSON 的场景，都在 prompt 中给出字段 Schema，并要求仅输出 JSON。
"""

from __future__ import annotations

# 分步讲解标签约定（写入 system prompt，移动端按 <step> 标签切分渲染）
STEP_INSTRUCTION = (
    "讲解题目或知识点时必须分步，每一步用 <step>第n步：……</step> 标签包裹；"
    "先讲思路再给答案，语言符合学生年级水平。"
)

# 聊天 system prompt 骨架，由 memory_manager.build_system_prompt 填充
BASE_SYSTEM_TEMPLATE = """你是一名专业的 AI 学习助手，正在为学生提供一对一辅导。

【学生信息】
- 姓名/称呼：{student_name}
- 年级：{grade}
- 学科偏好：{subject_preference}

【薄弱知识点】（讲解时重点关注、主动铺垫）
{weakness_block}

【学习偏好】
{preference_block}

【学生画像】
{profile_block}

{memory_block}

【输出要求】
{step_instruction}
回答使用中文；涉及计算时可调用工具，不要臆造计算结果。"""

MEMORY_EXTRACTION_PROMPT = """你是记忆提取器。请从下面的师生对话中找出"值得长期记住"的学生事实。

值得记住的类型：
- preference：显式学习偏好（如"喜欢用例题讲""不要英文回答"）
- profile：隐式画像（如"初三""基础薄弱""备战中考""学习进度到二次函数"）
- weakness：暴露出的薄弱知识点（如"分式方程去分母常漏乘"）

不要记录：一次性问题、寒暄、具体题目的答案。

请仅输出 JSON 数组（无其它文字），元素格式：
{{"type": "preference|profile|weakness", "content": "一句话事实", "importance": 0.0-1.0}}
若没有值得记住的内容，输出空数组 []。

对话记录：
{dialogue}"""

MISTAKE_ANALYSIS_PROMPT = """你是错题判定器。分析最近一轮学生作答与正确解析，判断学生是否答错。

请仅输出 JSON（无其它文字），格式：
{{"is_mistake": true/false,
  "question": "题目原文（概括亦可）",
  "user_answer": "学生的错误答案",
  "correct_answer": "正确答案",
  "analysis": "错因分析，一句话",
  "knowledge_tags": ["知识点1", "知识点2"]}}
若学生没有作答或答对了，输出 {{"is_mistake": false}}。

对话记录：
{dialogue}"""

VARIATION_PROMPT = """基于下面这道错题，生成 2 道考查相同知识点的变式练习（难度相近、数字/情境不同）。
每道题包含题目、选项（若为选择题）与答案解析；用 <step> 标签分步讲解解析过程。

原题：{question}
正确答案：{correct_answer}
错因：{analysis}
知识点：{knowledge_tags}"""

PPT_OUTLINE_PROMPT = """为 {grade}{subject} 课程《{topic}》设计一份教学 PPT 大纲，风格：{style}，共 {pages} 页。

请仅输出 JSON（无其它文字），格式：
{{"slides": [{{"slide_number": 1, "title": "页标题", "content_points": ["要点1", "要点2"], "notes": "讲师备注"}}]}}
要求：第 1 页为封面，末页为总结/作业；每页 3-6 个要点；slides 数组长度必须为 {pages}。"""

LESSON_PLAN_PROMPT = """为 {grade}《{course_name}》编写教案，课时时长 {duration} 分钟，模板类型：{template_type}。
教学目标参考：{objectives}

请仅输出 JSON（无其它文字），格式：
{{"objectives": ["目标1", "目标2"],
  "key_points": ["重点1"],
  "difficulties": ["难点1"],
  "procedure": [{{"stage": "环节名", "duration_minutes": 10, "activities": "师生活动描述"}}],
  "board_design": "板书设计",
  "homework": "作业布置",
  "reflection": "教学反思要点"}}
procedure 各环节时长之和须等于 {duration} 分钟。"""

EXAM_PAPER_PROMPT = """为 {grade}{subject} 出一份试卷，题型：{question_types}，整体难度：{difficulty}，共 {count} 题。

请仅输出 JSON（无其它文字），格式：
{{"questions": [{{"number": 1, "type": "single_choice|multiple_choice|fill_blank|true_false|short_answer|calculation",
  "question": "题干", "options": ["A. …", "B. …"],
  "answer": "答案", "analysis": "解析", "knowledge_tag": "知识点", "score": 5}}]}}
要求：题目互不重复；选择题 options 至少 2 项；知识点覆盖尽量均衡；难度以 {difficulty} 为主；questions 数组长度必须为 {count}。"""

ANALYSIS_REPORT_PROMPT = """基于以下班级学情统计数据，生成学情分析报告。

班级：{class_name}，分析类型：{analysis_type}
统计数据：
{stats}

请仅输出 JSON（无其它文字），格式：
{{"heat_map_data": [{{"label": "知识点", "value": 0.0-1.0}}],
  "trend_data": [{{"date": "YYYY-MM-DD", "value": 数值}}],
  "weak_points": ["薄弱点1"],
  "suggestions": ["教学建议1"]}}"""

PLAN_SCHEDULE_PROMPT = """为学生制定学习计划。
目标：{goal_description}
当前水平：{current_level}
总天数：{days} 天

请仅输出 JSON 数组（无其它文字），每天一项：
[{{"day": 1, "task": "当日学习任务", "duration": 60}}]
duration 单位为分钟，每天 30-120 分钟；任务由易到难、含复习巩固环节。"""

FLASHCARD_PROMPT = """从以下内容提炼 {count} 张记忆卡片（问答对），覆盖关键概念与易错点。

请仅输出 JSON 数组（无其它文字）：
[{{"front": "问题/提示", "back": "答案/解释"}}]

内容：
{content}"""

RAG_CONTEXT_TEMPLATE = """【参考资料】（回答时按需引用，标注引用编号如 [1]）
{chunks}"""

VOICE_EVAL_PROMPT = """你是英语口语评分官。请根据场景「{scenario}」评估学生的口语转写文本。

请仅输出 JSON 对象（无其它文字），各维度 0-100 分：
{{"pronunciation": 0, "grammar": 0, "vocabulary": 0, "fluency": 0, "overall": 0, "feedback": "中文总评（100 字以内，给出主要问题与改进建议）"}}

overall 为四维加权综合分；若转写文本为空或过短无法评判，各维度给 0 分并在 feedback 说明。"""
