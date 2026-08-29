import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/app_config.dart';
import '../storage/secure_storage.dart';

/// 统一 API 异常
class ApiException implements Exception {
  const ApiException(this.message, {this.statusCode, this.code});

  final String message;
  final int? statusCode;

  /// 服务端错误码（如 captcha_required / account_locked），无则为 null
  final String? code;

  factory ApiException.fromDio(DioException e) {
    final status = e.response?.statusCode;
    final data = e.response?.data;
    final serverMsg = data is Map<String, dynamic>
        ? data['message'] as String?
        : null;
    final serverCode =
        data is Map<String, dynamic> ? data['code'] as String? : null;
    final message = switch (e.type) {
      DioExceptionType.connectionTimeout ||
      DioExceptionType.sendTimeout ||
      DioExceptionType.receiveTimeout =>
        '网络超时，请稍后再试',
      DioExceptionType.connectionError => '网络连接失败，请检查网络',
      _ => serverMsg ?? '服务异常（${status ?? '未知'}）',
    };
    return ApiException(message, statusCode: status, code: serverCode);
  }

  @override
  String toString() => message;
}

/// 鉴权拦截器：附加 JWT、401 自动刷新并重试、错误统一映射。
/// 使用 QueuedInterceptor 串行化刷新，避免并发请求重复刷新 token。
class AuthInterceptor extends QueuedInterceptor {
  AuthInterceptor({required this.storage, required this.onSessionExpired});

  final SecureStorage storage;

  /// 刷新失败（会话过期）时回调，用于跳转登录
  final Future<void> Function() onSessionExpired;

  /// 独立的刷新用 Dio，避免触发自身拦截器死循环
  final Dio _refreshDio = Dio(BaseOptions(baseUrl: AppConfig.apiBaseUrl));

  @override
  Future<void> onRequest(
      RequestOptions options, RequestInterceptorHandler handler) async {
    final token = await storage.readAccessToken();
    if (token != null && token.isNotEmpty) {
      options.headers['Authorization'] = 'Bearer $token';
    }
    handler.next(options);
  }

  @override
  Future<void> onError(
      DioException err, ErrorInterceptorHandler handler) async {
    final retried = err.requestOptions.extra['__retried'] == true;
    if (err.response?.statusCode == 401 && !retried) {
      final newToken = await _refresh();
      if (newToken != null) {
        try {
          final opts = err.requestOptions
            ..headers['Authorization'] = 'Bearer $newToken'
            ..extra['__retried'] = true;
          final resp = await _refreshDio.fetch<dynamic>(opts);
          return handler.resolve(resp);
        } on DioException catch (e) {
          return handler.next(e);
        }
      }
      await onSessionExpired();
      return handler.next(err);
    }
    handler.next(
        err.copyWith(error: ApiException.fromDio(err)));
  }

  /// 刷新 access token，成功返回新 token，失败返回 null
  /// 契约（BFF RefreshRequest/TokenResponse）：snake_case 键位
  Future<String?> _refresh() async {
    final refresh = await storage.readRefreshToken();
    if (refresh == null || refresh.isEmpty) return null;
    try {
      final resp = await _refreshDio.post<Map<String, dynamic>>(
        '${AppConfig.apiPrefix}/auth/refresh',
        data: {'refresh_token': refresh},
      );
      final access = resp.data?['access_token'] as String?;
      final newRefresh = resp.data?['refresh_token'] as String? ?? refresh;
      if (access == null) return null;
      await storage.saveTokens(access: access, refresh: newRefresh);
      return access;
    } on DioException {
      return null;
    }
  }
}

/// 全局 Dio 实例 provider。
final dioProvider = Provider<Dio>((ref) {
  final dio = Dio(BaseOptions(
    baseUrl: AppConfig.apiBaseUrl,
    connectTimeout: const Duration(seconds: 15),
    receiveTimeout: const Duration(seconds: 30),
  ));
  dio.interceptors.add(AuthInterceptor(
    storage: ref.read(secureStorageProvider),
    onSessionExpired: () async {
      // 会话过期：清空登录态，路由层监听 authProvider 自动回登录页
      await ref.read(secureStorageProvider).clearAll();
      ref.read(sessionExpiredProvider.notifier).state = true;
    },
  ));
  if (kDebugMode) {
    dio.interceptors.add(LogInterceptor(requestBody: false, responseBody: false));
  }
  return dio;
});

/// 会话过期信号，authProvider 监听后登出
final sessionExpiredProvider = StateProvider<bool>((ref) => false);
