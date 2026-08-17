import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../models/user.dart';
import '../../config/app_config.dart';
import '../dio_client.dart';

/// 登录/注册返回的令牌对
class AuthTokens {
  const AuthTokens({
    required this.accessToken,
    required this.refreshToken,
    required this.user,
  });

  final String accessToken;
  final String refreshToken;
  final UserProfile user;
}

/// 鉴权 API（手写 client，未使用代码生成——见 README 说明）
///
/// 后端契约（BFF）：
/// - POST /auth/register → {user: UserOut, tokens: {access_token, refresh_token, ...}}
/// - POST /auth/login    → {access_token, refresh_token, ...}（不含 user，需再取 /users/me）
/// - GET  /users/me      → UserOut
/// JWT 无状态，无 /auth/logout。
class AuthApi {
  AuthApi(this._dio);

  final Dio _dio;

  /// username 即手机号（后端 RegisterRequest/LoginRequest 字段为 phone）
  Future<AuthTokens> login(String username, String password) async {
    final resp = await _dio.post<Map<String, dynamic>>(
      '${AppConfig.apiPrefix}/auth/login',
      data: {'phone': username, 'password': password},
    );
    final accessToken = resp.data!['access_token'] as String;
    final refreshToken = resp.data!['refresh_token'] as String;
    // 登录响应不含用户资料，用新令牌拉取 /users/me
    final user = await _fetchMe(accessToken);
    return AuthTokens(
      accessToken: accessToken,
      refreshToken: refreshToken,
      user: user,
    );
  }

  /// nickname 仅本地展示（users 表暂无 nickname 列，服务端不存储）；
  /// 非学生身份注册后另行切换 mode（PUT /users/me/mode）。
  Future<AuthTokens> register({
    required String username,
    required String password,
    required String nickname,
    required UserMode mode,
  }) async {
    final resp = await _dio.post<Map<String, dynamic>>(
      '${AppConfig.apiPrefix}/auth/register',
      data: {'phone': username, 'password': password},
    );
    final data = resp.data!;
    final tokens = data['tokens'] as Map<String, dynamic>;
    final accessToken = tokens['access_token'] as String;
    final refreshToken = tokens['refresh_token'] as String;
    var user = UserProfile.fromJson(data['user'] as Map<String, dynamic>)
        .copyWith(nickname: nickname);
    if (mode != UserMode.student) {
      try {
        await _dio.put<void>(
          '${AppConfig.apiPrefix}/users/me/mode',
          data: {'mode': mode.name},
          options: Options(headers: {'Authorization': 'Bearer $accessToken'}),
        );
        user = user.copyWith(mode: mode);
      } catch (_) {
        // 模式切换失败不阻塞注册流程，用户可在"我的"页重试
      }
    }
    return AuthTokens(
      accessToken: accessToken,
      refreshToken: refreshToken,
      user: user,
    );
  }

  Future<UserProfile> me() async {
    final resp =
        await _dio.get<Map<String, dynamic>>('${AppConfig.apiPrefix}/users/me');
    return UserProfile.fromJson(resp.data!);
  }

  /// 用指定令牌取资料（登录/注册后、令牌写入安全存储之前使用）
  Future<UserProfile> _fetchMe(String accessToken) async {
    final resp = await _dio.get<Map<String, dynamic>>(
      '${AppConfig.apiPrefix}/users/me',
      options: Options(headers: {'Authorization': 'Bearer $accessToken'}),
    );
    return UserProfile.fromJson(resp.data!);
  }

  /// 登出：JWT 无状态，后端无登出接口，仅本地清除令牌即可
  ///（清 token 逻辑在 auth_provider.logout / secureStorage.clearAll）。
  Future<void> logout() async {}
}

final authApiProvider = Provider<AuthApi>((ref) => AuthApi(ref.read(dioProvider)));
