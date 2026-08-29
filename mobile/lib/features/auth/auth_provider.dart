import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api/auth_api.dart';
import '../../core/network/api/user_api.dart';
import '../../core/network/dio_client.dart';
import '../../core/storage/secure_storage.dart';
import '../../models/user.dart';
import '../profile/profile_provider.dart';

/// 登录态：unknown=启动中；guest=游客（不强制登录）；authenticated=正式账号
enum AuthStatus { unknown, authenticated, guest, unauthenticated }

class AuthState {
  const AuthState({required this.status, this.user, this.error});

  final AuthStatus status;
  final UserProfile? user;
  final String? error;

  bool get isGuest => status == AuthStatus.guest;

  AuthState copyWith({AuthStatus? status, UserProfile? user, String? error}) {
    return AuthState(
      status: status ?? this.status,
      user: user ?? this.user,
      error: error,
    );
  }
}

class AuthNotifier extends StateNotifier<AuthState> {
  AuthNotifier(this._ref)
      : super(const AuthState(status: AuthStatus.unknown)) {
    _bootstrap();
    // 拦截器检测到会话过期时按原身份降级处理
    _ref.listen(sessionExpiredProvider, (prev, next) {
      if (next) _onSessionExpired();
    });
  }

  final Ref _ref;

  Future<void> _bootstrap() async {
    final token = await _ref.read(secureStorageProvider).readAccessToken();
    if (token == null || token.isEmpty) {
      // 无令牌：不强制登录，直接进入游客模式
      await enterGuest();
      return;
    }
    try {
      final user = await _ref.read(authApiProvider).me();
      _applyUser(user);
    } catch (_) {
      // token 无效/网络失败：尝试游客模式，失败才回登录页
      await enterGuest();
    }
  }

  void _applyUser(UserProfile user) {
    state = AuthState(
      status: user.isGuest ? AuthStatus.guest : AuthStatus.authenticated,
      user: user,
    );
    _ref.read(userModeProvider.notifier).state = user.mode;
  }

  /// 游客模式：设备 ID 换令牌；同设备重进可找回同一游客账号（含历史数据）
  Future<String?> enterGuest() async {
    try {
      final deviceId =
          await _ref.read(secureStorageProvider).getOrCreateDeviceId();
      final tokens = await _ref.read(authApiProvider).guestLogin(deviceId);
      await _ref
          .read(secureStorageProvider)
          .saveTokens(access: tokens.accessToken, refresh: tokens.refreshToken);
      _applyUser(tokens.user);
      return null;
    } on ApiException catch (e) {
      state = const AuthState(status: AuthStatus.unauthenticated);
      return e.message;
    } catch (_) {
      // 离线等场景：落到登录页，可重试游客进入或账号登录
      state = const AuthState(status: AuthStatus.unauthenticated);
      return '游客模式暂不可用，请检查网络后重试';
    }
  }

  /// 密码登录；失败抛 ApiException（code 可为 captcha_required / account_locked）
  Future<void> login(
    String username,
    String password, {
    String? captchaId,
    String? captchaCode,
  }) async {
    try {
      final deviceId =
          await _ref.read(secureStorageProvider).getOrCreateDeviceId();
      final tokens = await _ref.read(authApiProvider).login(
            username,
            password,
            deviceId: deviceId,
            captchaId: captchaId,
            captchaCode: captchaCode,
          );
      await _ref
          .read(secureStorageProvider)
          .saveTokens(access: tokens.accessToken, refresh: tokens.refreshToken);
      _applyUser(tokens.user);
    } on ApiException {
      rethrow; // 携带错误码（captcha_required 等），交由页面分支处理
    } catch (_) {
      throw const ApiException('登录失败，请稍后再试');
    }
  }

  /// 短信验证码登录
  Future<String?> loginSms(String phone, String code) async {
    try {
      final deviceId =
          await _ref.read(secureStorageProvider).getOrCreateDeviceId();
      final tokens =
          await _ref.read(authApiProvider).loginSms(phone, code, deviceId: deviceId);
      await _ref
          .read(secureStorageProvider)
          .saveTokens(access: tokens.accessToken, refresh: tokens.refreshToken);
      _applyUser(tokens.user);
      return null;
    } on ApiException catch (e) {
      return e.message;
    } catch (_) {
      return '登录失败，请稍后再试';
    }
  }

  Future<String?> register({
    required String username,
    required String password,
    required String nickname,
    required UserMode mode,
    required String smsCode,
  }) async {
    try {
      final deviceId =
          await _ref.read(secureStorageProvider).getOrCreateDeviceId();
      final tokens = await _ref.read(authApiProvider).register(
          username: username,
          password: password,
          nickname: nickname,
          mode: mode,
          smsCode: smsCode,
          deviceId: deviceId);
      await _ref
          .read(secureStorageProvider)
          .saveTokens(access: tokens.accessToken, refresh: tokens.refreshToken);
      _applyUser(tokens.user);
      return null;
    } on ApiException catch (e) {
      return e.message;
    } catch (_) {
      return '注册失败，请稍后再试';
    }
  }

  /// 游客转正：绑定手机号 + 密码，游客期间数据全部保留
  Future<String?> upgradeGuest({
    required String phone,
    required String password,
    required String smsCode,
  }) async {
    try {
      final tokens = await _ref
          .read(authApiProvider)
          .upgradeGuest(phone: phone, password: password, smsCode: smsCode);
      await _ref
          .read(secureStorageProvider)
          .saveTokens(access: tokens.accessToken, refresh: tokens.refreshToken);
      _applyUser(tokens.user);
      return null;
    } on ApiException catch (e) {
      return e.message;
    } catch (_) {
      return '绑定失败，请稍后再试';
    }
  }

  /// 切换身份（学生/教师/家长），同步服务端并驱动全局 UI
  Future<void> setMode(UserMode mode) async {
    _ref.read(userModeProvider.notifier).state = mode;
    final user = state.user;
    if (user == null) return;
    state = state.copyWith(user: user.copyWith(mode: mode));
    try {
      await _ref.read(userApiProvider).updateProfile({'mode': mode.name});
    } catch (_) {
      // 离线时仅本地生效
    }
  }

  Future<void> updateModel(String model) async {
    final user = state.user;
    if (user == null) return;
    state = state.copyWith(user: user.copyWith(selectedModel: model));
    try {
      await _ref.read(userApiProvider).updateProfile({'selectedModel': model});
    } catch (_) {}
  }

  /// 会话过期：游客静默重进游客模式（设备 ID 不变，数据不丢）；正式账号回登录页
  Future<void> _onSessionExpired() async {
    _ref.read(sessionExpiredProvider.notifier).state = false;
    await _ref.read(secureStorageProvider).clearTokens();
    if (state.status == AuthStatus.guest) {
      await enterGuest();
    } else {
      state = const AuthState(status: AuthStatus.unauthenticated);
    }
  }

  Future<void> logout() async {
    try {
      await _ref.read(authApiProvider).logout();
    } catch (_) {
      // 忽略登出接口失败
    }
    // 保留 device_id 与本机 API Key 副本，仅清除会话令牌
    await _ref.read(secureStorageProvider).clearTokens();
    state = const AuthState(status: AuthStatus.unauthenticated);
  }
}

final authProvider =
    StateNotifierProvider<AuthNotifier, AuthState>((ref) => AuthNotifier(ref));
