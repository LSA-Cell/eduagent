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

/// 设备会话条目（GET /auth/devices）
class DeviceSession {
  const DeviceSession({
    required this.sid,
    required this.deviceId,
    required this.deviceName,
    required this.lastSeen,
    required this.current,
  });

  final String sid;
  final String deviceId;
  final String deviceName;
  final DateTime? lastSeen;
  final bool current;

  factory DeviceSession.fromApi(Map<String, dynamic> json) {
    final ts = json['last_seen'] as int? ?? 0;
    return DeviceSession(
      sid: json['sid'] as String,
      deviceId: json['device_id'] as String? ?? '',
      deviceName: json['device_name'] as String? ?? '',
      lastSeen: ts > 0 ? DateTime.fromMillisecondsSinceEpoch(ts * 1000) : null,
      current: json['current'] as bool? ?? false,
    );
  }
}

/// 图形验证码（GET /auth/captcha）
class CaptchaChallenge {
  const CaptchaChallenge({required this.id, required this.svg});

  final String id;
  final String svg;
}

/// 鉴权 API（手写 client，未使用代码生成——见 README 说明）
///
/// 后端契约（BFF）：
/// - POST /auth/register       → {user, tokens}（需短信验证码）
/// - POST /auth/login          → TokenResponse（不含 user，需再取 /users/me）
/// - POST /auth/login-sms      → TokenResponse（验证码登录，未注册自动建号）
/// - POST /auth/guest          → TokenResponse（游客，设备 ID 换令牌）
/// - POST /auth/upgrade        → {user, tokens}（游客转正）
/// - POST /auth/logout         → 204（吊销当前设备会话 + access 黑名单）
/// - POST /auth/sms-code       → {expires_in[, dev_code]}
/// - GET  /auth/captcha        → {captcha_id, svg, expires_in}
/// - POST /auth/password/change|reset → 204
/// - GET  /users/me            → UserOut
class AuthApi {
  AuthApi(this._dio);

  final Dio _dio;

  /// 统一把 DioException 翻译成携带服务端错误码的 ApiException
  Never _rethrow(Object e) {
    if (e is DioException) {
      final inner = e.error;
      if (inner is ApiException) throw inner;
      throw ApiException.fromDio(e);
    }
    throw e;
  }

  Map<String, dynamic> _deviceFields(String? deviceId) =>
      {if (deviceId != null) 'device_id': deviceId};

  /// username 即手机号（后端 LoginRequest 字段为 phone）；
  /// captchaId/captchaCode 在连续失败后由服务端要求（captcha_required）。
  Future<AuthTokens> login(
    String username,
    String password, {
    String? deviceId,
    String? captchaId,
    String? captchaCode,
  }) async {
    try {
      final resp = await _dio.post<Map<String, dynamic>>(
        '${AppConfig.apiPrefix}/auth/login',
        data: {
          'phone': username,
          'password': password,
          ..._deviceFields(deviceId),
          if (captchaId != null) 'captcha_id': captchaId,
          if (captchaCode != null) 'captcha_code': captchaCode,
        },
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
    } catch (e) {
      _rethrow(e);
    }
  }

  /// 短信验证码登录（未注册自动建号）
  Future<AuthTokens> loginSms(String phone, String code,
      {String? deviceId}) async {
    try {
      final resp = await _dio.post<Map<String, dynamic>>(
        '${AppConfig.apiPrefix}/auth/login-sms',
        data: {'phone': phone, 'code': code, ..._deviceFields(deviceId)},
      );
      final accessToken = resp.data!['access_token'] as String;
      final refreshToken = resp.data!['refresh_token'] as String;
      final user = await _fetchMe(accessToken);
      return AuthTokens(
          accessToken: accessToken, refreshToken: refreshToken, user: user);
    } catch (e) {
      _rethrow(e);
    }
  }

  /// 游客登录：设备 ID 换令牌，同设备重进找回同一游客账号
  Future<AuthTokens> guestLogin(String deviceId) async {
    try {
      final resp = await _dio.post<Map<String, dynamic>>(
        '${AppConfig.apiPrefix}/auth/guest',
        data: {'device_id': deviceId},
      );
      final accessToken = resp.data!['access_token'] as String;
      final refreshToken = resp.data!['refresh_token'] as String;
      final user = await _fetchMe(accessToken);
      return AuthTokens(
          accessToken: accessToken, refreshToken: refreshToken, user: user);
    } catch (e) {
      _rethrow(e);
    }
  }

  /// 游客转正：绑定手机号 + 密码（短信验证码核验），游客数据保留
  Future<AuthTokens> upgradeGuest({
    required String phone,
    required String password,
    required String smsCode,
  }) async {
    try {
      final resp = await _dio.post<Map<String, dynamic>>(
        '${AppConfig.apiPrefix}/auth/upgrade',
        data: {'phone': phone, 'password': password, 'sms_code': smsCode},
      );
      final data = resp.data!;
      final tokens = data['tokens'] as Map<String, dynamic>;
      return AuthTokens(
        accessToken: tokens['access_token'] as String,
        refreshToken: tokens['refresh_token'] as String,
        user: UserProfile.fromJson(data['user'] as Map<String, dynamic>),
      );
    } catch (e) {
      _rethrow(e);
    }
  }

  /// nickname 仅本地展示（users 表暂无 nickname 列，服务端不存储）；
  /// 非学生身份注册后另行切换 mode（PUT /users/me/mode）。
  Future<AuthTokens> register({
    required String username,
    required String password,
    required String nickname,
    required UserMode mode,
    required String smsCode,
    String? deviceId,
  }) async {
    try {
      final resp = await _dio.post<Map<String, dynamic>>(
        '${AppConfig.apiPrefix}/auth/register',
        data: {
          'phone': username,
          'password': password,
          'sms_code': smsCode,
          ..._deviceFields(deviceId),
        },
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
    } catch (e) {
      _rethrow(e);
    }
  }

  /// 下发短信验证码（scene=register/login/reset），返回重发间隔秒数
  Future<int> sendSmsCode(String phone, String scene) async {
    try {
      final resp = await _dio.post<Map<String, dynamic>>(
        '${AppConfig.apiPrefix}/auth/sms-code',
        data: {'phone': phone, 'scene': scene},
      );
      return resp.data?['expires_in'] as int? ?? 300;
    } catch (e) {
      _rethrow(e);
    }
  }

  /// 获取图形验证码（登录连续失败后服务端要求）
  Future<CaptchaChallenge> fetchCaptcha() async {
    try {
      final resp = await _dio
          .get<Map<String, dynamic>>('${AppConfig.apiPrefix}/auth/captcha');
      return CaptchaChallenge(
        id: resp.data!['captcha_id'] as String,
        svg: resp.data!['svg'] as String,
      );
    } catch (e) {
      _rethrow(e);
    }
  }

  /// 修改密码（登录态）
  Future<void> changePassword(String oldPassword, String newPassword) async {
    try {
      await _dio.post<void>(
        '${AppConfig.apiPrefix}/auth/password/change',
        data: {'old_password': oldPassword, 'new_password': newPassword},
      );
    } catch (e) {
      _rethrow(e);
    }
  }

  /// 找回密码（短信验证码核验）
  Future<void> resetPassword(
      String phone, String smsCode, String newPassword) async {
    try {
      await _dio.post<void>(
        '${AppConfig.apiPrefix}/auth/password/reset',
        data: {'phone': phone, 'sms_code': smsCode, 'new_password': newPassword},
      );
    } catch (e) {
      _rethrow(e);
    }
  }

  /// 设备管理：列出当前账号的活跃登录设备
  Future<List<DeviceSession>> listDevices() async {
    try {
      final resp = await _dio
          .get<List<dynamic>>('${AppConfig.apiPrefix}/auth/devices');
      return [
        for (final d in resp.data ?? const [])
          DeviceSession.fromApi(d as Map<String, dynamic>),
      ];
    } catch (e) {
      _rethrow(e);
    }
  }

  /// 踢人下线：吊销指定设备会话
  Future<void> revokeDevice(String sid) async {
    try {
      await _dio.delete<void>('${AppConfig.apiPrefix}/auth/devices/$sid');
    } catch (e) {
      _rethrow(e);
    }
  }

  Future<UserProfile> me() async {
    try {
      final resp =
          await _dio.get<Map<String, dynamic>>('${AppConfig.apiPrefix}/users/me');
      return UserProfile.fromJson(resp.data!);
    } catch (e) {
      _rethrow(e);
    }
  }

  /// 用指定令牌取资料（登录/注册后、令牌写入安全存储之前使用）
  Future<UserProfile> _fetchMe(String accessToken) async {
    final resp = await _dio.get<Map<String, dynamic>>(
      '${AppConfig.apiPrefix}/users/me',
      options: Options(headers: {'Authorization': 'Bearer $accessToken'}),
    );
    return UserProfile.fromJson(resp.data!);
  }

  /// 登出：服务端吊销当前设备会话 + access token 黑名单；
  /// 失败不阻塞本地清理（清 token 逻辑在 auth_provider.logout）。
  Future<void> logout() async {
    try {
      await _dio.post<void>('${AppConfig.apiPrefix}/auth/logout');
    } catch (_) {}
  }
}

final authApiProvider = Provider<AuthApi>((ref) => AuthApi(ref.read(dioProvider)));
