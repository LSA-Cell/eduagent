import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api/billing_api.dart';
import '../../models/billing.dart';

/// 在售套餐列表
final plansProvider = FutureProvider.autoDispose<List<PlanInfo>>((ref) async {
  return ref.read(billingApiProvider).listPlans();
});

/// 当前订阅 + 本期额度用量
final subscriptionProvider =
    FutureProvider.autoDispose<SubscriptionStatus>((ref) async {
  return ref.read(billingApiProvider).getSubscription();
});

/// mock 购买（dev/test）：下单 → mock 支付确认 → 刷新订阅状态。
/// 真实支付渠道接入后替换为 IAP/收银台流程（见 指导/后端设计/计费/spec.md §1.2）。
Future<void> mockPurchase(WidgetRef ref, String planId) async {
  final api = ref.read(billingApiProvider);
  final order = await api.createOrder(planId);
  await api.mockConfirm(order.id);
  ref.invalidate(subscriptionProvider);
}
