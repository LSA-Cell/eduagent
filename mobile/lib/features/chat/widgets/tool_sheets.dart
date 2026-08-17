import 'package:flutter/material.dart';
import 'package:flutter_math_fork/flutter_math.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../../app/theme.dart';
import '../../../core/network/api/user_api.dart';
import '../../plans/plans_page.dart';
import '../providers/chat_provider.dart';
import 'generation_sheets.dart';
import 'sheet_header.dart';

/// 直接触发型工具的参数收集 Sheet 入口（按工具 id 分发）。
/// 上传类/路由类/开关类工具不走这里，见 edu_toolbar.dart。
void openToolSheet(
  BuildContext context, {
  required String toolId,
  required String toolLabel,
}) {
  showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (sheetContext) => Padding(
      // 键盘弹出时抬高 Sheet
      padding:
          EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(sheetContext).bottom),
      child: SingleChildScrollView(
        child: switch (toolId) {
          'formula' => const FormulaEditorSheet(),
          'chemistry' => const PromptToolSheet(
              toolId: 'chemistry',
              title: '化学方程式',
              hint: '输入化学物质或反应，如“配平 Al + O₂ → Al₂O₃”“实验室制取 CO₂”',
            ),
          'essay_coach' => const PromptToolSheet(
              toolId: 'essay_coach',
              title: '作文辅导',
              hint: '输入作文题目或要求，如“以《那一刻，我长大了》为题写记叙文”',
            ),
          'knowledge_check' => const KnowledgeCheckSheet(),
          'study_plan' => StudyPlanSheet(
              onCreated: () {
                ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('学习计划已创建')));
                context.push('/plans');
              },
            ),
          'make_ppt' || 'make_lesson_plan' || 'make_exam' =>
            GenerationFormSheet(toolId: toolId, toolLabel: toolLabel),
          'analytics' => AnalyticsSheet(
              onGenerated: (report) =>
                  context.push('/teacher/report', extra: report),
            ),
          _ => const SizedBox.shrink(),
        },
      ),
    ),
  );
}

/// 公式助手：手写板占位 + LaTeX 键盘输入 + 实时预览，插入聊天输入框
class FormulaEditorSheet extends ConsumerStatefulWidget {
  const FormulaEditorSheet({super.key});

  @override
  ConsumerState<FormulaEditorSheet> createState() =>
      _FormulaEditorSheetState();
}

class _FormulaEditorSheetState extends ConsumerState<FormulaEditorSheet> {
  final _ctrl = TextEditingController();

  /// 常用 LaTeX 符号快捷键盘
  static const _symbols = [
    '+', '-', r'\times', r'\div', '=', r'^', r'_',
    r'\sqrt{}', r'\frac{}{}', r'\pi', r'\sum', r'\int',
    '(', ')', '{', '}',
  ];

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  /// 在光标处插入符号；含 {} 的模板把光标移入第一对花括号内
  void _insertSymbol(String symbol) {
    final value = _ctrl.value;
    final sel = value.selection;
    final start = sel.isValid ? sel.start : value.text.length;
    final end = sel.isValid ? sel.end : value.text.length;
    final braceIndex = symbol.indexOf('{}');
    final offset = braceIndex >= 0
        ? start + braceIndex + 1
        : start + symbol.length;
    _ctrl.value = TextEditingValue(
      text: value.text.replaceRange(start, end, symbol),
      selection: TextSelection.collapsed(offset: offset),
    );
  }

  /// 包成 $...$ 写入插入信号，由 ChatInput 监听并插入光标处
  void _insertToInput() {
    final latex = _ctrl.text.trim();
    if (latex.isEmpty) return;
    ref.read(chatInputInsertProvider.notifier).state = '\$$latex\$';
    Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final latex = _ctrl.text.trim();
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SheetHeader(icon: Icons.functions, title: '公式助手'),
          const SizedBox(height: 14),

          // 手写板占位：后续接手写识别，当前仅作区域示意
          Container(
            height: 120,
            decoration: BoxDecoration(
              border: Border.all(color: scheme.outlineVariant),
              borderRadius: BorderRadius.circular(AppTokens.radiusMd),
              color: scheme.surfaceContainerHighest.withValues(alpha: 0.4),
            ),
            child: Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.gesture, color: scheme.outline),
                  const SizedBox(height: 4),
                  Text('手写识别区（即将上线）',
                      style: Theme.of(context).textTheme.labelSmall),
                  Text('可先使用下方键盘输入 LaTeX',
                      style: Theme.of(context).textTheme.labelSmall),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),

          // 常用符号快捷键盘
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final s in _symbols)
                ActionChip(
                  label: Text(s, style: const TextStyle(fontSize: 12)),
                  visualDensity: VisualDensity.compact,
                  onPressed: () => _insertSymbol(s),
                ),
            ],
          ),
          const SizedBox(height: 12),

          TextField(
            controller: _ctrl,
            autofocus: true,
            decoration: const InputDecoration(
              labelText: 'LaTeX 公式',
              hintText: r'如：x = \frac{-b \pm \sqrt{b^2-4ac}}{2a}',
            ),
            onChanged: (_) => setState(() {}), // 刷新预览
          ),
          const SizedBox(height: 12),

          // 实时预览（解析失败降级为原文）
          Container(
            width: double.infinity,
            constraints: const BoxConstraints(minHeight: 56),
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: scheme.surfaceContainerLowest,
              border: Border.all(color: scheme.outlineVariant),
              borderRadius: BorderRadius.circular(AppTokens.radiusMd),
            ),
            child: latex.isEmpty
                ? Text('预览区',
                    style: TextStyle(color: scheme.outline, fontSize: 12))
                : SingleChildScrollView(
                    scrollDirection: Axis.horizontal,
                    child: Math.tex(
                      latex,
                      mathStyle: MathStyle.text,
                      onErrorFallback: (_) => Text(latex),
                    ),
                  ),
          ),
          const SizedBox(height: 16),

          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              TextButton(
                onPressed: () => Navigator.of(context).pop(),
                child: const Text('取消'),
              ),
              const SizedBox(width: 8),
              FilledButton.icon(
                onPressed: latex.isEmpty ? null : _insertToInput,
                icon: const Icon(Icons.input, size: 18),
                label: const Text('插入输入框'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// 通用提示词 Sheet（化学方程式 / 作文辅导）：输入需求 → 触发工具对话
class PromptToolSheet extends ConsumerStatefulWidget {
  const PromptToolSheet({
    super.key,
    required this.toolId,
    required this.title,
    required this.hint,
  });

  final String toolId;
  final String title;
  final String hint;

  @override
  ConsumerState<PromptToolSheet> createState() => _PromptToolSheetState();
}

class _PromptToolSheetState extends ConsumerState<PromptToolSheet> {
  final _ctrl = TextEditingController();

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final text = _ctrl.text.trim();
    if (text.isEmpty) return;
    // 先取 notifier 再关 Sheet，避免 unmount 后使用 ref
    final notifier = ref.read(chatProvider.notifier);
    Navigator.of(context).pop();
    // 插入“正在使用…”系统消息并调用工具，结果以卡片消息追加
    await notifier.invokeTool(widget.toolId, widget.title,
        payload: {'prompt': text});
  }

  IconData get _icon => switch (widget.toolId) {
        'chemistry' => Icons.science,
        'essay_coach' => Icons.edit_note,
        _ => Icons.auto_awesome,
      };

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SheetHeader(icon: _icon, title: widget.title),
          const SizedBox(height: 14),
          TextField(
            controller: _ctrl,
            autofocus: true,
            minLines: 2,
            maxLines: 5,
            decoration: InputDecoration(hintText: widget.hint),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 16),
          Align(
            alignment: Alignment.centerRight,
            child: FilledButton.icon(
              onPressed: _ctrl.text.trim().isEmpty ? null : _submit,
              icon: const Icon(Icons.send, size: 18),
              label: const Text('开始'),
            ),
          ),
        ],
      ),
    );
  }
}

/// 知识点检测：选学科（可填具体知识点）→ 触发 Agent 出题
class KnowledgeCheckSheet extends ConsumerStatefulWidget {
  const KnowledgeCheckSheet({super.key});

  @override
  ConsumerState<KnowledgeCheckSheet> createState() =>
      _KnowledgeCheckSheetState();
}

class _KnowledgeCheckSheetState extends ConsumerState<KnowledgeCheckSheet> {
  static const _subjects = ['数学', '语文', '英语', '物理', '化学', '生物', '历史', '地理'];

  final _topicCtrl = TextEditingController();
  String _subject = _subjects.first;

  @override
  void dispose() {
    _topicCtrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final notifier = ref.read(chatProvider.notifier);
    final topic = _topicCtrl.text.trim();
    Navigator.of(context).pop();
    await notifier.invokeTool('knowledge_check', '知识点检测', payload: {
      'subject': _subject,
      if (topic.isNotEmpty) 'topic': topic,
    });
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SheetHeader(
            icon: Icons.quiz,
            title: '知识点检测',
            subtitle: '选择学科后由 Agent 自动出题',
          ),
          const SizedBox(height: 14),
          Wrap(
            spacing: 8,
            runSpacing: 4,
            children: [
              for (final s in _subjects)
                ChoiceChip(
                  label: Text(s),
                  selected: _subject == s,
                  onSelected: (_) => setState(() => _subject = s),
                ),
            ],
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _topicCtrl,
            decoration: const InputDecoration(
              labelText: '知识点（可选）',
              hintText: '如：一元二次方程；留空则综合出题',
            ),
          ),
          const SizedBox(height: 16),
          Align(
            alignment: Alignment.centerRight,
            child: FilledButton.icon(
              onPressed: _submit,
              icon: const Icon(Icons.quiz, size: 18),
              label: const Text('开始出题'),
            ),
          ),
        ],
      ),
    );
  }
}

/// 学习计划：目标填写 → 创建计划，成功后跳转计划页
class StudyPlanSheet extends ConsumerStatefulWidget {
  const StudyPlanSheet({super.key, this.onCreated});

  /// 创建成功回调（外层负责提示与跳转）
  final VoidCallback? onCreated;

  @override
  ConsumerState<StudyPlanSheet> createState() => _StudyPlanSheetState();
}

class _StudyPlanSheetState extends ConsumerState<StudyPlanSheet> {
  static const _subjects = ['数学', '语文', '英语', '物理', '化学', '生物', '历史', '地理'];
  static const _minuteOptions = [15, 30, 45, 60, 90, 120];

  final _goalCtrl = TextEditingController();
  String _subject = _subjects.first;
  int _dailyMinutes = 30;
  DateTime? _deadline;
  bool _submitting = false;

  @override
  void dispose() {
    _goalCtrl.dispose();
    super.dispose();
  }

  Future<void> _pickDeadline() async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: _deadline ?? now.add(const Duration(days: 7)),
      firstDate: now,
      lastDate: now.add(const Duration(days: 365)),
    );
    if (picked != null) setState(() => _deadline = picked);
  }

  Future<void> _create() async {
    final goal = _goalCtrl.text.trim();
    if (goal.isEmpty || _submitting) return;
    setState(() => _submitting = true);
    try {
      await ref.read(userApiProvider).createPlan({
        'title': goal,
        'subject': _subject,
        'dailyMinutes': _dailyMinutes,
        if (_deadline != null) 'deadline': _deadline!.toIso8601String(),
      });
      ref.invalidate(plansProvider); // 计划页下次打开时刷新
      if (!mounted) return;
      Navigator.of(context).pop();
      widget.onCreated?.call();
    } catch (_) {
      if (mounted) {
        setState(() => _submitting = false);
        ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('创建失败，请检查网络后重试')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SheetHeader(icon: Icons.event_note, title: '制定学习计划'),
          const SizedBox(height: 14),
          TextField(
            controller: _goalCtrl,
            autofocus: true,
            decoration: const InputDecoration(
              labelText: '学习目标',
              hintText: '如：期末数学上 90 分',
            ),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            value: _subject,
            decoration: const InputDecoration(labelText: '学科'),
            items: [
              for (final s in _subjects)
                DropdownMenuItem(value: s, child: Text(s)),
            ],
            onChanged: (v) => setState(() => _subject = v ?? _subjects.first),
          ),
          const SizedBox(height: 12),
          Text('每日可投入时间', style: Theme.of(context).textTheme.labelMedium),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            children: [
              for (final m in _minuteOptions)
                ChoiceChip(
                  label: Text('$m 分钟'),
                  selected: _dailyMinutes == m,
                  onSelected: (_) => setState(() => _dailyMinutes = m),
                ),
            ],
          ),
          const SizedBox(height: 8),
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.event),
            title: Text(_deadline == null
                ? '截止日期（可选）'
                : '截止 ${DateFormat('yyyy-MM-dd').format(_deadline!)}'),
            trailing: _deadline == null
                ? null
                : IconButton(
                    icon: const Icon(Icons.clear, size: 18),
                    onPressed: () => setState(() => _deadline = null),
                  ),
            onTap: _pickDeadline,
          ),
          const SizedBox(height: 8),
          Align(
            alignment: Alignment.centerRight,
            child: FilledButton.icon(
              onPressed:
                  _goalCtrl.text.trim().isEmpty || _submitting ? null : _create,
              icon: _submitting
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.event_note, size: 18),
              label: Text(_submitting ? '创建中…' : '创建计划'),
            ),
          ),
        ],
      ),
    );
  }
}
