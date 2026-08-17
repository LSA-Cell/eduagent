import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api/user_api.dart';
import '../../models/mistake.dart';
import '../chat/providers/chat_provider.dart';
import '../chat/widgets/markdown_math.dart';

/// 错题列表 provider（带学科筛选）
final mistakesProvider =
    FutureProvider.autoDispose<List<Mistake>>((ref) async {
  final subject = ref.watch(mistakeSubjectProvider);
  return ref.read(userApiProvider).listMistakes(subject: subject);
});

final mistakeSubjectProvider = StateProvider<String?>((ref) => null);

/// 错题本页：学科筛选 + 讲解 + 变式练习入口
class MistakesPage extends ConsumerWidget {
  const MistakesPage({super.key});

  static const _subjects = ['全部', '数学', '语文', '英语', '物理', '化学'];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final selected = ref.watch(mistakeSubjectProvider);
    final mistakes = ref.watch(mistakesProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('错题本')),
      body: Column(
        children: [
          SizedBox(
            height: 48,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12),
              children: [
                for (final s in _subjects)
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: FilterChip(
                      label: Text(s),
                      selected: (s == '全部' && selected == null) || s == selected,
                      onSelected: (_) => ref
                          .read(mistakeSubjectProvider.notifier)
                          .state = s == '全部' ? null : s,
                    ),
                  ),
              ],
            ),
          ),
          Expanded(
            child: mistakes.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (e, _) => Center(child: Text('加载失败：$e')),
              data: (list) => list.isEmpty
                  ? const _EmptyHint(icon: Icons.task_alt, text: '暂无错题，继续保持')
                  : RefreshIndicator(
                      onRefresh: () => ref.refresh(mistakesProvider.future),
                      child: ListView.builder(
                        padding: const EdgeInsets.all(12),
                        itemCount: list.length,
                        itemBuilder: (context, i) =>
                            _MistakeCard(mistake: list[i]),
                      ),
                    ),
            ),
          ),
        ],
      ),
    );
  }
}

class _MistakeCard extends ConsumerWidget {
  const _MistakeCard({required this.mistake});

  final Mistake mistake;

  Color _masteryColor(ColorScheme scheme) => mistake.mastery >= 80
      ? Colors.green
      : mistake.mastery >= 60
          ? Colors.orange
          : scheme.error;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final scheme = Theme.of(context).colorScheme;
    final masteryColor = _masteryColor(scheme);
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: scheme.secondaryContainer.withValues(alpha: 0.6),
                    borderRadius: BorderRadius.circular(999),
                  ),
                  child: Text(mistake.subject,
                      style: TextStyle(
                          fontSize: 12,
                          fontWeight: FontWeight.w500,
                          color: scheme.onSecondaryContainer)),
                ),
                const Spacer(),
                // 掌握度：按区间着色
                Icon(Icons.gps_fixed, size: 13, color: masteryColor),
                const SizedBox(width: 4),
                Text('掌握度 ${mistake.mastery}%',
                    style: Theme.of(context)
                        .textTheme
                        .labelSmall
                        ?.copyWith(color: masteryColor)),
              ],
            ),
            const SizedBox(height: 10),
            MarkdownMath(data: mistake.question),
            const SizedBox(height: 6),
            const Divider(height: 16),
            Row(
              children: [
                TextButton.icon(
                  icon: const Icon(Icons.lightbulb_outline, size: 18),
                  label: const Text('讲解'),
                  onPressed: () => _showAnalysis(context),
                ),
                TextButton.icon(
                  icon: const Icon(Icons.autorenew, size: 18),
                  label: const Text('变式练习'),
                  onPressed: () async {
                    await ref
                        .read(chatProvider.notifier)
                        .startVariantPractice(mistake.id, mistake.question);
                    if (context.mounted) context.go('/chat');
                  },
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  void _showAnalysis(BuildContext context) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (context) => DraggableScrollableSheet(
        initialChildSize: 0.6,
        expand: false,
        builder: (context, scrollController) => SingleChildScrollView(
          controller: scrollController,
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(Icons.lightbulb,
                      size: 20, color: Theme.of(context).colorScheme.primary),
                  const SizedBox(width: 8),
                  Text('题目讲解',
                      style: Theme.of(context).textTheme.titleMedium),
                ],
              ),
              const SizedBox(height: 14),
              MarkdownMath(
                  data: mistake.analysis.isEmpty
                      ? '正确答案：${mistake.correctAnswer}'
                      : mistake.analysis),
              const SizedBox(height: 12),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: Theme.of(context)
                      .colorScheme
                      .surfaceContainerHighest
                      .withValues(alpha: 0.5),
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Text('我的答案：${mistake.myAnswer}',
                    style: Theme.of(context).textTheme.bodySmall),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 空状态：图标圆底 + 提示文案
class _EmptyHint extends StatelessWidget {
  const _EmptyHint({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 72,
            height: 72,
            decoration: BoxDecoration(
              color: scheme.primaryContainer.withValues(alpha: 0.5),
              shape: BoxShape.circle,
            ),
            child: Icon(icon, size: 34, color: scheme.primary),
          ),
          const SizedBox(height: 14),
          Text(text, style: Theme.of(context).textTheme.bodyMedium),
        ],
      ),
    );
  }
}
