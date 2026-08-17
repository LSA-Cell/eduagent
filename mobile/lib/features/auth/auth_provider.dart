import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api/auth_api.dart';
import '../../core/network/api/user_api.dart';
import '../../core/network/dio_client.dart';
import '../../core/storage/secure_storage.dart';
import '../../models/user.dart';
import '../profile/profile_provider.dart';

enum AuthStatus { unknown, authenticated, unauthenticated }

class AuthState {
  const AuthState({required this.status, this.user, this.error});

  final AuthStatus status;
  final UserProfile? user;
  final String? error;

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
    // 拦截器检测到会话过期时自动登出
    _ref.listen(sessionExpiredProvider, (prev, next) {
      if (next) _clearSession();
    });
  }

  final Ref _ref;

  Future<void> _bootstrap() async {
    final token = await _ref.read(secureStorageProvider).readAccessToken();
    if (token == null || token.isEmpty) {
      state = const AuthState(status: AuthStatus.unauthenticated);
      return;
    }
    try {
      final user = await _ref.read(authApiProvider).me();
      _applyUser(user);
    } catch (_) {
      // token 无效/网络失败：按未登录处理，由用户重新登录
      state = const AuthState(status: AuthStatus.unauthenticated);
    }
  }

  void _applyUser(UserProfile user) {
    state = AuthState(status: AuthStatus.authenticated, user: user);
    _ref.read(userModeProvider.notifier).state = user.mode;
  }

  Future<String?> login(String username, String password) async {
    try {
      final tokens = await _ref.read(authApiProvider).login(username, password);
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
  }) async {
    try {
      final tokens = await _ref.read(authApiProvider).register(
          username: username, password: password, nickname: nickname, mode: mode);
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

  Future<void> _clearSession() async {
    _ref.read(sessionExpiredProvider.notifier).state = false;
    state = const AuthState(status: AuthStatus.unauthenticated);
  }

  Future<void> logout() async {
    try {
      await _ref.read(authApiProvider).logout();
    } catch (_) {
      // 忽略登出接口失败
    }
    await _ref.read(secureStorageProvider).clearAll();
    state = const AuthState(status: AuthStatus.unauthenticated);
  }
}

final authProvider =
    StateNotifierProvider<AuthNotifier, AuthState>((ref) => AuthNotifier(ref));
