/// Billing 领域模型（契约见 指导/后端设计/计费/spec.md §4）。
///
/// 额度单位 = USD 成本美分；套餐售价 = CNY 分（两者解耦）。
library;

/// 套餐
class PlanInfo {
  const PlanInfo({
    required this.id,
    required this.name,
    required this.priceCents,
    required this.currency,
    required this.monthlyQuotaCents,
    required this.allowedModels,
    required this.features,
    required this.sortOrder,
  });

  final String id;
  final String name;
  final int priceCents;
  final String currency;
  final int monthlyQuotaCents;
  final List<String> allowedModels;
  final Map<String, dynamic> features;
  final int sortOrder;

  /// 月价展示文案，如 ¥68.00/月；免费档返回「免费」
  String get priceLabel =>
      priceCents <= 0 ? '免费' : '¥${(priceCents / 100).toStringAsFixed(2)}/月';

  factory PlanInfo.fromJson(Map<String, dynamic> json) => PlanInfo(
        id: json['id'] as String,
        name: json['name'] as String,
        priceCents: (json['price_cents'] as num).toInt(),
        currency: json['currency'] as String? ?? 'CNY',
        monthlyQuotaCents: (json['monthly_quota_cents'] as num).toInt(),
        allowedModels:
            (json['allowed_models'] as List<dynamic>? ?? const []).cast<String>(),
        features:
            (json['features'] as Map<String, dynamic>?) ?? const <String, dynamic>{},
        sortOrder: (json['sort_order'] as num?)?.toInt() ?? 0,
      );
}

/// 本期额度用量
class QuotaInfo {
  const QuotaInfo({
    required this.periodStart,
    required this.quotaCents,
    required this.usedCents,
    required this.remainingCents,
  });

  final String periodStart;
  final int quotaCents;
  final int usedCents;
  final int remainingCents;

  /// 0~1 使用进度（quota 为 0 时视为已用尽）
  double get usageRatio =>
      quotaCents <= 0 ? 1.0 : (usedCents / quotaCents).clamp(0.0, 1.0);

  /// 额度展示（USD 成本），如 $3.00
  String get quotaLabel => '\$${(quotaCents / 100).toStringAsFixed(2)}';
  String get usedLabel => '\$${(usedCents / 100).toStringAsFixed(2)}';

  factory QuotaInfo.fromJson(Map<String, dynamic> json) => QuotaInfo(
        periodStart: json['period_start'] as String,
        quotaCents: (json['quota_cents'] as num).toInt(),
        usedCents: (json['used_cents'] as num).toInt(),
        remainingCents: (json['remaining_cents'] as num).toInt(),
      );
}

/// 当前订阅状态（无付费订阅时 status=free，plan 为免费档）
class SubscriptionStatus {
  const SubscriptionStatus({
    required this.plan,
    required this.status,
    this.channel,
    this.currentPeriodStart,
    this.currentPeriodEnd,
    required this.autoRenew,
    required this.quota,
  });

  final PlanInfo plan;
  final String status; // active / free / expired / canceled
  final String? channel;
  final String? currentPeriodStart;
  final String? currentPeriodEnd;
  final bool autoRenew;
  final QuotaInfo quota;

  bool get isPaid => status == 'active';

  factory SubscriptionStatus.fromJson(Map<String, dynamic> json) =>
      SubscriptionStatus(
        plan: PlanInfo.fromJson(json['plan'] as Map<String, dynamic>),
        status: json['status'] as String,
        channel: json['channel'] as String?,
        currentPeriodStart: json['current_period_start'] as String?,
        currentPeriodEnd: json['current_period_end'] as String?,
        autoRenew: json['auto_renew'] as bool? ?? true,
        quota: QuotaInfo.fromJson(json['quota'] as Map<String, dynamic>),
      );
}

/// 下单结果（含渠道支付参数；mock 渠道含 mock_token）
class OrderResult {
  const OrderResult({
    required this.id,
    required this.planId,
    required this.amountCents,
    required this.currency,
    required this.channel,
    required this.status,
    required this.paymentParams,
  });

  final String id;
  final String planId;
  final int amountCents;
  final String currency;
  final String channel;
  final String status;
  final Map<String, dynamic> paymentParams;

  factory OrderResult.fromJson(Map<String, dynamic> json) => OrderResult(
        id: json['id'] as String,
        planId: json['plan_id'] as String,
        amountCents: (json['amount_cents'] as num).toInt(),
        currency: json['currency'] as String? ?? 'CNY',
        channel: json['channel'] as String,
        status: json['status'] as String,
        paymentParams:
            (json['payment_params'] as Map<String, dynamic>?) ??
                const <String, dynamic>{},
      );
}
