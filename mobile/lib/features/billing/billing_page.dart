import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/billing.dart';
import 'billing_provider.dart';

/// 「会员与额度」页：当前订阅状态 + 套餐选购。
///
/// 视觉纪律与全 App 一致（frontend-design）：纯描边图标、无渐变填充，
/// 唯一签名单色元素是套餐卡片的状态 chip。
class BillingPage extends ConsumerWidget {
  const BillingPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final subscription = ref.watch(subscriptionProvider);
    final plans = ref.watch(plansProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('会员与额度')),
      body: RefreshIndicator(
        onRefresh: () async {
          ref.invalidate(subscriptionProvider);
          ref.invalidate(plansProvider);
        },
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          children: [
            subscription.when(
              data: (s) => _SubscriptionCard(status: s),
              loading: () => const Card(
                child: Padding(
                  padding: EdgeInsets.all(32),
                  child: Center(child: CircularProgressIndicator()),
                ),
              ),
              error: (e, _) => Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Text('订阅状态加载失败：$e'),
                ),
              ),
            ),
            const SizedBox(height: 20),
            Text('选择套餐', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            plans.when(
              data: (list) => Column(
                children: [
                  for (final plan in list)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: _PlanCard(
                        plan: plan,
                        currentPlanId:
                            subscription.valueOrNull?.plan.id ?? 'free',
                        isPaidActive: subscription.valueOrNull?.isPaid ?? false,
                      ),
                    ),
                ],
              ),
              loading: () => const Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              ),
              error: (e, _) => Padding(
                padding: const EdgeInsets.all(16),
                child: Text('套餐加载失败：$e'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 当前订阅状态卡片：套餐 / 周期 / 额度进度
class _SubscriptionCard extends StatelessWidget {
  const _SubscriptionCard({required this.status});

  final SubscriptionStatus status;

  String _statusLabel() => switch (status.status) {
        'active' => '生效中',
        'free' => '免费版',
        'canceled' => '已取消续订',
        'expired' => '已过期',
        _ => status.status,
      };

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final quota = status.quota;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  width: 44,
                  height: 44,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    border: Border.all(color: scheme.primary, width: 1.5),
                  ),
                  child: Icon(Icons.workspace_premium_outlined,
                      color: scheme.primary),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(status.plan.name,
                          style: Theme.of(context).textTheme.titleMedium),
                      const SizedBox(height: 2),
                      Text(
                        status.isPaid && status.currentPeriodEnd != null
                            ? '有效期至 ${status.currentPeriodEnd!.substring(0, 10)}'
                            : '所有用户默认享有免费额度',
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    ],
                  ),
                ),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                  decoration: BoxDecoration(
                    color: scheme.primaryContainer.withValues(alpha: 0.55),
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Text(_statusLabel(),
                      style: TextStyle(fontSize: 12, color: scheme.primary)),
                ),
              ],
            ),
            const SizedBox(height: 16),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text('本月平台额度',
                    style: Theme.of(context).textTheme.bodySmall),
                Text('${quota.usedLabel} / ${quota.quotaLabel}',
                    style: Theme.of(context).textTheme.bodySmall),
              ],
            ),
            const SizedBox(height: 6),
            LinearProgressIndicator(
              value: quota.usageRatio,
              minHeight: 6,
              borderRadius: BorderRadius.circular(3),
            ),
          ],
        ),
      ),
    );
  }
}

/// 套餐卡片：价格 / 额度 / 功能点 / 购买按钮
class _PlanCard extends ConsumerStatefulWidget {
  const _PlanCard({
    required this.plan,
    required this.currentPlanId,
    required this.isPaidActive,
  });

  final PlanInfo plan;
  final String currentPlanId;
  final bool isPaidActive;

  @override
  ConsumerState<_PlanCard> createState() => _PlanCardState();
}

class _PlanCardState extends ConsumerState<_PlanCard> {
  bool _buying = false;

  bool get _isCurrent =>
      widget.plan.id == widget.currentPlanId &&
      (widget.plan.id != 'free' || !widget.isPaidActive);

  List<String> get _featureLabels {
    final f = widget.plan.features;
    return [
      if (f['rag_max_docs'] != null) '知识库文档 ${f['rag_max_docs']} 篇',
      if (f['docgen_per_month'] != null) '文档生成 ${f['docgen_per_month']} 次/月',
      if (f['teacher_tools'] == true) '教师专属工具',
      if (widget.plan.allowedModels.isEmpty && widget.plan.id != 'free')
        '全部模型可用',
    ];
  }

  Future<void> _buy() async {
    setState(() => _buying = true);
    try {
      await mockPurchase(ref, widget.plan.id);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('已开通${widget.plan.name}（mock 支付）')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('购买失败：$e')),
        );
      }
    } finally {
      if (mounted) setState(() => _buying = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final quotaDollars =
        (widget.plan.monthlyQuotaCents / 100).toStringAsFixed(0);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(widget.plan.name,
                    style: Theme.of(context).textTheme.titleMedium),
                Text(widget.plan.priceLabel,
                    style: Theme.of(context)
                        .textTheme
                        .titleMedium
                        ?.copyWith(color: scheme.primary)),
              ],
            ),
            const SizedBox(height: 8),
            Text('每月平台额度 \$$quotaDollars',
                style: Theme.of(context).textTheme.bodySmall),
            if (_featureLabels.isNotEmpty) ...[
              const SizedBox(height: 6),
              for (final label in _featureLabels)
                Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Row(
                    children: [
                      Icon(Icons.check_circle_outline,
                          size: 14, color: scheme.primary),
                      const SizedBox(width: 6),
                      Text(label,
                          style: Theme.of(context).textTheme.bodySmall),
                    ],
                  ),
                ),
            ],
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              child: widget.plan.id == 'free'
                  ? const OutlinedButton(
                      onPressed: null, child: Text('默认享有'))
                  : _isCurrent
                      ? const OutlinedButton(
                          onPressed: null, child: Text('当前套餐'))
                      : FilledButton.tonal(
                          onPressed: _buying ? null : _buy,
                          child: _buying
                              ? const SizedBox(
                                  width: 18,
                                  height: 18,
                                  child:
                                      CircularProgressIndicator(strokeWidth: 2),
                                )
                              : Text(widget.isPaidActive ? '换购' : '升级'),
                        ),
            ),
          ],
        ),
      ),
    );
  }
}
