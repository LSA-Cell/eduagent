/// 应用配置：通过 --dart-define 注入，缺省指向本地 BFF。
class AppConfig {
  const AppConfig._();

  /// BFF 基础地址，如 http://localhost:8000
  static const String apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://localhost:8000',
  );

  /// API 前缀
  static const String apiPrefix = '/api/v1';

  /// SSE 心跳超时
  static const Duration sseTimeout = Duration(seconds: 60);

  static String url(String path) => '$apiBaseUrl$apiPrefix$path';
}
