import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/theme.dart';
import '../../../core/network/api/teacher_api.dart';
import '../../../core/network/api/tool_api.dart';
import '../../../core/network/api/user_api.dart';
import '../../../models/analytics.dart';
import '../../../models/mistake.dart';
import '../providers/chat_provider.dart';
import 'sheet_header.dart';

/// 学情分析 Sheet 用的错题列表（多选来源）
final _sheetMistakesProvider = FutureProvider.autoDispose<List<Mistake>>(
    (ref) => ref.read(userApiProvider).listMistakes());

/// 文档生成参数表单（制作 PPT / 制作教案 / 生成试卷共用）。
/// 提交后由 chatProvider 创建生成任务并轮询，完成插入预览卡片消息。
class GenerationFormSheet extends ConsumerStatefulWidget {
  const GenerationFormSheet({
    super.key,
    required this.toolId,
    required this.toolLabel,
  });

  /// make_ppt / make_lesson_plan / make_exam
  final String toolId;
  final String toolLabel;

  @override
  ConsumerState<GenerationFormSheet> createState() =>
      _GenerationFormSheetState();
}

class _GenerationFormSheetState extends ConsumerState<GenerationFormSheet> {
  static const _subjects = ['数学', '语文', '英语', '物理', '化学', '生物', '历史', '地理'];
  static const _styles = ['简约', '学术', '活泼'];
  static const _grades = ['小学', '初中', '高中', '大学'];
  static const _difficulties = ['简单', '中等', '困难'];

  final _topicCtrl = TextEditingController();

  // PPT 参数
  double _pageCount = 12;
  String _style = _styles.first;
  // 教案参数
  String _grade = '高中';
  int _classHours = 1;
  // 试卷参数
  String _subject = _subjects.first;
  double _questionCount = 20;
  String _difficulty = _difficulties[1];

  @override
  void dispose() {
    _topicCtrl.dispose();
    super.dispose();
  }

  String get _topicLabel => switch (widget.toolId) {
        'make_ppt' => '课件主题',
        'make_lesson_plan' => '教案课题',
        _ => '考查范围',
      };

  String get _topicHint => switch (widget.toolId) {
        'make_ppt' => '如：高一物理·牛顿第二定律',
        'make_lesson_plan' => '如：初中语文·《背影》',
        _ => '如：一元二次方程',
      };

  /// 组装参数 → 创建生成任务（后台轮询，结果进聊天区）
  void _submit() {
    final topic = _topicCtrl.text.trim();
    if (topic.isEmpty) return;
    final payload = <String, dynamic>{'topic': topic};
    switch (widget.toolId) {
      case 'make_ppt':
        payload['pageCount'] = _pageCount.round();
        payload['style'] = _style;
        payload['grade'] = _grade; // 后端 PresentationCreateRequest 必填
        payload['subject'] = _subject;
      case 'make_lesson_plan':
        payload['grade'] = _grade;
        payload['classHours'] = _classHours;
      case 'make_exam':
        payload['grade'] = _grade; // 后端 ExamPaperCreateRequest 必填
        payload['subject'] = _subject;
        payload['questionCount'] = _questionCount.round();
        payload['difficulty'] = _difficulty;
    }
    final notifier = ref.read(chatProvider.notifier);
    Navigator.of(context).pop();
    notifier.runGenerationTask(widget.toolId, widget.toolLabel, payload);
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SheetHeader(
            icon: switch (widget.toolId) {
              'make_ppt' => Icons.slideshow,
              'make_lesson_plan' => Icons.menu_book,
              _ => Icons.assignment,
            },
            title: widget.toolLabel,
          ),
          const SizedBox(height: 14),
          TextField(
            controller: _topicCtrl,
            autofocus: true,
            decoration:
                InputDecoration(labelText: _topicLabel, hintText: _topicHint),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 12),

          // 按工具显示各自参数
          if (widget.toolId == 'make_ppt') ...[
            _ChipRow(
              label: '学段',
              options: _grades,
              selected: _grade,
              onSelected: (v) => setState(() => _grade = v),
            ),
            _ChipRow(
              label: '学科',
              options: _subjects,
              selected: _subject,
              onSelected: (v) => setState(() => _subject = v),
            ),
            _SliderRow(
              label: '页数',
              value: _pageCount,
              min: 5,
              max: 30,
              divisions: 25,
              text: '${_pageCount.round()} 页',
              onChanged: (v) => setState(() => _pageCount = v),
            ),
            _ChipRow(
              label: '风格',
              options: _styles,
              selected: _style,
              onSelected: (v) => setState(() => _style = v),
            ),
          ] else if (widget.toolId == 'make_lesson_plan') ...[
            _ChipRow(
              label: '学段',
              options: _grades,
              selected: _grade,
              onSelected: (v) => setState(() => _grade = v),
            ),
            _ChipRow(
              label: '课时',
              options: const ['1 课时', '2 课时', '3 课时'],
              selected: '$_classHours 课时',
              onSelected: (v) =>
                  setState(() => _classHours = int.parse(v.substring(0, 1))),
            ),
          ] else if (widget.toolId == 'make_exam') ...[
            _ChipRow(
              label: '学段',
              options: _grades,
              selected: _grade,
              onSelected: (v) => setState(() => _grade = v),
            ),
            _ChipRow(
              label: '学科',
              options: _subjects,
              selected: _subject,
              onSelected: (v) => setState(() => _subject = v),
            ),
            _SliderRow(
              label: '题量',
              value: _questionCount,
              min: 5,
              max: 50,
              divisions: 9,
              text: '${_questionCount.round()} 题',
              onChanged: (v) => setState(() => _questionCount = v),
            ),
            _ChipRow(
              label: '难度',
              options: _difficulties,
              selected: _difficulty,
              onSelected: (v) => setState(() => _difficulty = v),
            ),
          ],
          const SizedBox(height: 16),

          Align(
            alignment: Alignment.centerRight,
            child: FilledButton.icon(
              onPressed: _topicCtrl.text.trim().isEmpty ? null : _submit,
              icon: const Icon(Icons.auto_awesome, size: 18),
              label: const Text('开始生成'),
            ),
          ),
        ],
      ),
    );
  }
}

/// 学情分析：上传成绩文件 / 勾选错题 → 生成报告 → 报告页
class AnalyticsSheet extends ConsumerStatefulWidget {
  const AnalyticsSheet({super.key, required this.onGenerated});

  /// 生成成功回调（外层负责跳转报告页）
  final ValueChanged<AnalyticsReport> onGenerated;

  @override
  ConsumerState<AnalyticsSheet> createState() => _AnalyticsSheetState();
}

class _AnalyticsSheetState extends ConsumerState<AnalyticsSheet> {
  /// 数据来源：file=上传成绩文件，mistakes=勾选错题
  String _source = 'file';
  String? _filePath;
  String? _fileName;
  final _selectedMistakeIds = <String>{};
  bool _generating = false;
  String? _error;

  Future<void> _pickFile() async {
    final files = await FilePicker.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['csv', 'xlsx', 'xls'],
    );
    if (files.isEmpty) return;
    final file = files.single;
    setState(() {
      _filePath = file.path;
      _fileName = file.name;
      _error = null;
    });
  }

  Future<void> _generate() async {
    if (_generating) return;
    if (_source == 'file' && _filePath == null) {
      setState(() => _error = '请先选择成绩文件');
      return;
    }
    if (_source == 'mistakes' && _selectedMistakeIds.isEmpty) {
      setState(() => _error = '请至少选择一道错题');
      return;
    }
    setState(() {
      _generating = true;
      _error = null;
    });
    try {
      // 成绩文件先上传换取 URL，再提交生成
      String? fileUrl;
      final path = _filePath;
      if (_source == 'file' && path != null) {
        fileUrl = await ref.read(toolApiProvider).uploadFile(path);
      }
      final report = await ref.read(teacherApiProvider).generateReport(
            fileUrl: fileUrl,
            mistakeIds:
                _source == 'mistakes' ? _selectedMistakeIds.toList() : null,
          );
      if (!mounted) return;
      Navigator.of(context).pop();
      widget.onGenerated(report);
    } catch (_) {
      if (mounted) {
        setState(() {
          _generating = false;
          _error = '报告生成失败，请稍后重试';
        });
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
          const SheetHeader(
            icon: Icons.insights,
            title: '学情分析',
            subtitle: '基于成绩文件或错题本生成班级/个人学情报告',
          ),
          const SizedBox(height: 14),

          // 数据来源切换
          SegmentedButton<String>(
            segments: const [
              ButtonSegment(
                  value: 'file',
                  label: Text('上传成绩文件'),
                  icon: Icon(Icons.upload_file, size: 18)),
              ButtonSegment(
                  value: 'mistakes',
                  label: Text('选择错题'),
                  icon: Icon(Icons.bookmark_outline, size: 18)),
            ],
            selected: {_source},
            onSelectionChanged: (s) => setState(() {
              _source = s.first;
              _error = null;
            }),
          ),
          const SizedBox(height: 12),

          if (_source == 'file')
            Container(
              decoration: BoxDecoration(
                border: Border.all(
                    color: Theme.of(context).colorScheme.outlineVariant),
                borderRadius: BorderRadius.circular(12),
              ),
              child: ListTile(
                leading: const Icon(Icons.folder_open),
                title: Text(_fileName ?? '选择成绩文件'),
                subtitle: const Text('支持 CSV / Excel（xlsx、xls）'),
                trailing: _filePath == null
                    ? null
                    : const Icon(Icons.check_circle, color: AppPalette.sage),
                onTap: _pickFile,
              ),
            )
          else
            ref.watch(_sheetMistakesProvider).when(
                  loading: () => const Padding(
                    padding: EdgeInsets.all(24),
                    child: Center(child: CircularProgressIndicator()),
                  ),
                  error: (e, _) => Text('错题加载失败：$e'),
                  data: (list) => list.isEmpty
                      ? const Text('暂无错题，可改用上传成绩文件')
                      : SizedBox(
                          height: 220,
                          child: ListView(
                            children: [
                              for (final m in list)
                                CheckboxListTile(
                                  dense: true,
                                  contentPadding: EdgeInsets.zero,
                                  value: _selectedMistakeIds.contains(m.id),
                                  onChanged: (on) => setState(() {
                                    if (on == true) {
                                      _selectedMistakeIds.add(m.id);
                                    } else {
                                      _selectedMistakeIds.remove(m.id);
                                    }
                                  }),
                                  title: Text(m.question,
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis),
                                  subtitle: Text(m.subject),
                                ),
                            ],
                          ),
                        ),
                ),

          if (_error != null) ...[
            const SizedBox(height: 8),
            Text(_error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ],
          const SizedBox(height: 16),

          Align(
            alignment: Alignment.centerRight,
            child: FilledButton.icon(
              onPressed: _generating ? null : _generate,
              icon: _generating
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.insights, size: 18),
              label: Text(_generating ? '生成中…' : '生成报告'),
            ),
          ),
        ],
      ),
    );
  }
}

/// 表单小部件：标签 + 横向 ChoiceChip 组
class _ChipRow extends StatelessWidget {
  const _ChipRow({
    required this.label,
    required this.options,
    required this.selected,
    required this.onSelected,
  });

  final String label;
  final List<String> options;
  final String selected;
  final ValueChanged<String> onSelected;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: Theme.of(context).textTheme.labelMedium),
        const SizedBox(height: 4),
        Wrap(
          spacing: 8,
          runSpacing: 4,
          children: [
            for (final o in options)
              ChoiceChip(
                label: Text(o),
                selected: selected == o,
                onSelected: (_) => onSelected(o),
              ),
          ],
        ),
        const SizedBox(height: 8),
      ],
    );
  }
}

/// 表单小部件：标签 + Slider + 当前值文本
class _SliderRow extends StatelessWidget {
  const _SliderRow({
    required this.label,
    required this.value,
    required this.min,
    required this.max,
    required this.divisions,
    required this.text,
    required this.onChanged,
  });

  final String label;
  final double value;
  final double min;
  final double max;
  final int divisions;
  final String text;
  final ValueChanged<double> onChanged;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text(label, style: Theme.of(context).textTheme.labelMedium),
            const Spacer(),
            Text(text, style: Theme.of(context).textTheme.labelSmall),
          ],
        ),
        Slider(
          value: value,
          min: min,
          max: max,
          divisions: divisions,
          onChanged: onChanged,
        ),
      ],
    );
  }
}
