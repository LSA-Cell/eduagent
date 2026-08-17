import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api/teacher_api.dart';
import '../../core/network/api/tool_api.dart';
import '../../models/analytics.dart';
import '../../models/tool_result.dart';
import '../chat/widgets/tool_cards.dart';
import 'widgets/analytics_board.dart';

final analyticsProvider =
    FutureProvider.autoDispose<AnalyticsData>((ref) async {
  return ref.read(teacherApiProvider).analytics();
});

/// 教师工作台：教案库 / 课件库 / 试卷库 / 学情看板 / 备课助手
class TeacherPage extends ConsumerWidget {
  const TeacherPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return DefaultTabController(
      length: 5,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('教师工作台'),
          bottom: const TabBar(
            isScrollable: true,
            tabs: [
              Tab(text: '教案库'),
              Tab(text: '课件库'),
              Tab(text: '试卷库'),
              Tab(text: '学情看板'),
              Tab(text: '备课助手'),
            ],
          ),
        ),
        body: TabBarView(
          children: [
            const _LibraryTab(kind: 'lesson_plan', icon: Icons.menu_book),
            const _LibraryTab(kind: 'courseware', icon: Icons.slideshow),
            const _LibraryTab(kind: 'exam', icon: Icons.assignment),
            const _AnalyticsTab(),
            _LessonAssistantTab(),
          ],
        ),
      ),
    );
  }
}

/// 资源库 Tab（教案/课件/试卷共用）
class _LibraryTab extends ConsumerWidget {
  const _LibraryTab({required this.kind, required this.icon});

  final String kind;
  final IconData icon;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final items = ref.watch(_libraryProvider(kind));
    return items.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (e, _) => Center(child: Text('加载失败：$e')),
      data: (list) => list.isEmpty
          ? Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Container(
                    width: 72,
                    height: 72,
                    decoration: BoxDecoration(
                      color: Theme.of(context)
                          .colorScheme
                          .primaryContainer
                          .withValues(alpha: 0.5),
                      shape: BoxShape.circle,
                    ),
                    child: Icon(icon,
                        size: 34,
                        color: Theme.of(context).colorScheme.primary),
                  ),
                  const SizedBox(height: 14),
                  const Text('暂无资源，可在"备课助手"中生成'),
                ],
              ),
            )
          : ListView.builder(
              padding: const EdgeInsets.all(12),
              itemCount: list.length,
              itemBuilder: (context, i) {
                final item = list[i];
                final scheme = Theme.of(context).colorScheme;
                return Card(
                  margin: const EdgeInsets.only(bottom: 10),
                  child: ListTile(
                    contentPadding:
                        const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
                    leading: Container(
                      width: 40,
                      height: 40,
                      decoration: BoxDecoration(
                        color: scheme.primaryContainer.withValues(alpha: 0.5),
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Icon(icon, size: 21, color: scheme.primary),
                    ),
                    title: Text(item['title'] as String? ?? '未命名',
                        maxLines: 1, overflow: TextOverflow.ellipsis),
                    subtitle: Text(item['updatedAt'] as String? ?? ''),
                    trailing: Icon(Icons.chevron_right,
                        color: scheme.onSurfaceVariant),
                    onTap: () {
                      // 资源详情由后端返回结构决定，后续接入预览页
                    },
                  ),
                );
              },
            ),
    );
  }
}

final _libraryProvider =
    FutureProvider.autoDispose.family<List<Map<String, dynamic>>, String>(
        (ref, kind) => ref.read(teacherApiProvider).listLibrary(kind));

class _AnalyticsTab extends ConsumerWidget {
  const _AnalyticsTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final analytics = ref.watch(analyticsProvider);
    return analytics.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (e, _) => Center(child: Text('加载失败：$e')),
      data: (data) => AnalyticsBoard(data: data),
    );
  }
}

/// 备课助手：输入主题一键生成教案，结果以教案卡片预览
class _LessonAssistantTab extends ConsumerStatefulWidget {
  @override
  ConsumerState<_LessonAssistantTab> createState() =>
      _LessonAssistantTabState();
}

class _LessonAssistantTabState extends ConsumerState<_LessonAssistantTab> {
  final _topicCtrl = TextEditingController();
  bool _generating = false;
  LessonPlanPreview? _result;
  String? _error;

  @override
  void dispose() {
    _topicCtrl.dispose();
    super.dispose();
  }

  Future<void> _generate() async {
    final topic = _topicCtrl.text.trim();
    if (topic.isEmpty) return;
    setState(() {
      _generating = true;
      _error = null;
    });
    try {
      // 异步生成：POST /teacher/lesson-plans（202）→ 轮询详情 → 预览卡片
      final api = ref.read(toolApiProvider);
      final pollPath = await api
          .createGenerationTask('make_lesson_plan', {'topic': topic, 'grade': '高中'});
      Map<String, dynamic>? detail;
      for (var attempt = 0; attempt < 30; attempt++) {
        await Future<void>.delayed(const Duration(seconds: 2));
        final task = await api.getGenerationTask(pollPath);
        final status = task['status'] as String? ?? 'generating';
        if (ToolApi.successStatuses.contains(status)) {
          detail = task;
          break;
        }
        if (status == 'failed') {
          throw StateError(task['error'] as String? ?? '生成失败');
        }
      }
      if (detail == null) throw StateError('生成超时，可稍后在教案库查看');
      setState(() =>
          _result = LessonPlanPreview.fromJson(ToolApi.previewOf('make_lesson_plan', detail!)));
    } catch (e) {
      setState(() => _error = '生成失败：$e');
    } finally {
      if (mounted) setState(() => _generating = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        TextField(
          controller: _topicCtrl,
          decoration: const InputDecoration(
            labelText: '课程主题',
            hintText: '如：高一物理·牛顿第二定律',
            prefixIcon: Icon(Icons.auto_awesome),
          ),
          onSubmitted: (_) => _generate(),
        ),
        const SizedBox(height: 12),
        FilledButton.icon(
          onPressed: _generating ? null : _generate,
          icon: _generating
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2))
              : const Icon(Icons.auto_awesome),
          label: Text(_generating ? '生成中…' : '生成教案'),
        ),
        if (_error != null) ...[
          const SizedBox(height: 12),
          Text(_error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error)),
        ],
        if (_result != null) ...[
          const SizedBox(height: 12),
          LessonPlanCard(data: _result!),
        ],
      ],
    );
  }
}
