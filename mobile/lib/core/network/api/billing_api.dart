import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../models/billing.dart';
import '../../config/app_config.dart';
import '../dio_client.dart';

/// 计费 API：套餐 / 订阅 / 下单 / mock 支付确认（走 BFF /billing/*）。
class BillingApi {
  BillingApi(this._dio);

  final Dio _dio;

  /// GET /billing/plans：在售套餐列表
  Future<List<PlanInfo>> listPlans() async {
    final resp =
        await _dio.get<List<dynamic>>('${AppConfig.apiPrefix}/billing/plans');
    return (resp.data ?? [])
        .map((e) => PlanInfo.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// GET /billing/subscription：当前订阅 + 本期额度用量
  Future<SubscriptionStatus> getSubscription() async {
    final resp = await _dio
        .get<Map<String, dynamic>>('${AppConfig.apiPrefix}/billing/subscription');
    return SubscriptionStatus.fromJson(resp.data!);
  }

  /// POST /billing/orders：下单（当前仅 mock 渠道已接入）
  Future<OrderResult> createOrder(String planId, {String channel = 'mock'}) async {
    final resp = await _dio.post<Map<String, dynamic>>(
      '${AppConfig.apiPrefix}/billing/orders',
      data: {'plan_id': planId, 'channel': channel},
    );
    return OrderResult.fromJson(resp.data!);
  }

  /// POST /billing/orders/{id}/mock-confirm：mock 支付确认（仅 dev/test 可用，
  /// 生产环境 BFF 返回 404；真实渠道接入后删除此调用）。
  Future<void> mockConfirm(String orderId) async {
    await _dio.post<void>(
      '${AppConfig.apiPrefix}/billing/orders/$orderId/mock-confirm',
    );
  }
}

final billingApiProvider =
    Provider<BillingApi>((ref) => BillingApi(ref.read(dioProvider)));
