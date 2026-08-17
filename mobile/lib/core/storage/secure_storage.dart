import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// token / API Key 安全存储。
/// 敏感信息只允许走 Keychain(iOS) / EncryptedSharedPreferences(Android)，
/// 禁止写入 Hive/SQLite 明文存储。
class SecureStorage {
  SecureStorage()
      : _storage = const FlutterSecureStorage(
          aOptions: AndroidOptions(),
        );

  final FlutterSecureStorage _storage;

  static const _kAccessToken = 'access_token';
  static const _kRefreshToken = 'refresh_token';
  static const _kApiKeyPrefix = 'apikey_'; // apikey_<provider>

  Future<String?> readAccessToken() => _storage.read(key: _kAccessToken);
  Future<String?> readRefreshToken() => _storage.read(key: _kRefreshToken);

  Future<void> saveTokens({required String access, required String refresh}) async {
    await _storage.write(key: _kAccessToken, value: access);
    await _storage.write(key: _kRefreshToken, value: refresh);
  }

  /// 用户自有 API Key（写入后即送往代理服务，客户端只保留安全存储副本）
  Future<void> saveApiKey(String provider, String key) =>
      _storage.write(key: '$_kApiKeyPrefix$provider', value: key);

  Future<String?> readApiKey(String provider) =>
      _storage.read(key: '$_kApiKeyPrefix$provider');

  Future<void> deleteApiKey(String provider) =>
      _storage.delete(key: '$_kApiKeyPrefix$provider');

  Future<void> clearAll() => _storage.deleteAll();
}

final secureStorageProvider = Provider<SecureStorage>((ref) => SecureStorage());
