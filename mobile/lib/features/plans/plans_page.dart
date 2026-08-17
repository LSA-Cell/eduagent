import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../../core/network/api/user_api.dart';
import '../../models/plan.dart';

final plansProvider =
    FutureProvider.autoDispose<List<StudyPlan>>((ref) async {
  return ref.read(userApiProvider).listPlans();
});

/// 学习计划页
class PlansPage extends ConsumerWidget {
  const PlansPage({super.key});

  Future<void> _toggleTask(WidgetRef ref, StudyPlan plan, PlanTask task) async {
    try {
      await ref
          .read(userApiProvider)
          .updatePlanTask(plan.id, task.id, !task.done);
    } catch (_) {
      // 离线时也刷新（本地乐观更新由后端补偿）
    }
    ref.invalidate(plansProvider);
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final plans = ref.watch(plansProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('学习计划')),
      body: plans.when(
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
                      child: Icon(Icons.event_note,
                          size: 34,
                          color: Theme.of(context).colorScheme.primary),
                    ),
                    const SizedBox(height: 14),
                    const Text('暂无计划，可在对话中说"帮我制定学习计划"'),
                  ],
                ),
              )
            : ListView.builder(
                padding: const EdgeInsets.all(12),
                itemCount: list.length,
                itemBuilder: (context, i) => _PlanCard(
                  plan: list[i],
                  onToggleTask: (task) => _toggleTask(ref, list[i], task),
                ),
              ),
      ),
    );
  }
}

class _PlanCard extends StatelessWidget {
  const _PlanCard({required this.plan, required this.onToggleTask});

  final StudyPlan plan;
  final ValueChanged<PlanTask> onToggleTask;

  @override
  Widget build(BuildContext context) {
    final progress = plan.progress;
    final scheme = Theme.of(context).colorScheme;
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(plan.title,
                      style: Theme.of(context).textTheme.titleMedium),
                ),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: scheme.secondaryContainer.withValues(alpha: 0.6),
                    borderRadius: BorderRadius.circular(999),
                  ),
                  child: Text(plan.subject,
                      style: TextStyle(
                          fontSize: 12,
                          fontWeight: FontWeight.w500,
                          color: scheme.onSecondaryContainer)),
                ),
              ],
            ),
            if (plan.deadline != null) ...[
              const SizedBox(height: 6),
              Row(
                children: [
                  Icon(Icons.event, size: 13, color: scheme.onSurfaceVariant),
                  const SizedBox(width: 4),
                  Text(
                    '截止 ${DateFormat('yyyy-MM-dd').format(plan.deadline!)}',
                    style: Theme.of(context).textTheme.labelSmall,
                  ),
                ],
              ),
            ],
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                    child: LinearProgressIndicator(
                        value: progress, minHeight: 8,
                        borderRadius: BorderRadius.circular(4))),
                const SizedBox(width: 10),
                Text('${(progress * 100).toStringAsFixed(0)}%',
                    style: Theme.of(context)
                        .textTheme
                        .labelMedium
                        ?.copyWith(
                            fontWeight: FontWeight.w600,
                            color: scheme.primary)),
              ],
            ),
            const SizedBox(height: 4),
            const Divider(height: 20),
            for (final task in plan.tasks)
              CheckboxListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                controlAffinity: ListTileControlAffinity.leading,
                title: Text(task.title,
                    style: TextStyle(
                        color: task.done ? scheme.onSurfaceVariant : null,
                        decoration:
                            task.done ? TextDecoration.lineThrough : null)),
                subtitle: Text('约 ${task.minutes} 分钟'),
                value: task.done,
                onChanged: (_) => onToggleTask(task),
              ),
          ],
        ),
      ),
    );
  }
}
