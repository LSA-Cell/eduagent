// Widget 测试用假网络层：替换 dioProvider，避免真实 HTTP 在 FakeAsync 下挂起。
import 'dart:typed_data';

import 'package:dio/dio.dart';

/// 立即返回空数据响应的适配器：
/// - GET 列表类接口返回空分页 {items, total, page, page_size}
/// - 其余方法返回空对象 {}
class FakeHttpAdapter implements HttpClientAdapter {
  @override
  Future<ResponseBody> fetch(RequestOptions options,
      Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    final body = options.method == 'GET'
        ? '{"items":[],"total":0,"page":1,"page_size":20}'
        : '{}';
    return ResponseBody.fromString(
      body,
      200,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

/// 测试用 Dio：无拦截器（不触碰 secure storage），baseUrl 与生产一致
Dio buildFakeDio() =>
    Dio(BaseOptions(baseUrl: 'http://localhost'))..httpClientAdapter = FakeHttpAdapter();
