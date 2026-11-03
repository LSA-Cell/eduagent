import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:eduagent_mobile/core/network/api/tool_api.dart';
import 'package:flutter_test/flutter_test.dart';

class RecordingAdapter implements HttpClientAdapter {
  RecordingAdapter(this.respond);

  final ResponseBody Function(RequestOptions) respond;
  final requests = <RequestOptions>[];

  @override
  Future<ResponseBody> fetch(RequestOptions options,
      Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    requests.add(options);
    return respond(options);
  }

  @override
  void close({bool force = false}) {}
}

ResponseBody jsonResponse(Map<String, dynamic> data, {int status = 200}) =>
    ResponseBody.fromString(jsonEncode(data), status, headers: {
      Headers.contentTypeHeader: ['application/json']
    });

void main() {
  late Directory temp;
  const signedPut = 'https://storage.example/u/1/file?X-Amz-Signature=put';
  const signedPost = 'https://storage.example/eduagent-uploads';
  const signedGet = 'https://storage.example/u/1/file?X-Amz-Signature=get';

  setUp(() async {
    temp = await Directory.systemTemp.createTemp('eduagent-upload-test-');
  });
  tearDown(() async => temp.delete(recursive: true));

  for (final sample in {
    'photo.jpg': 'chat_image',
    'voice.m4a': 'chat_audio',
    'scores.csv': 'report_file',
    'scores.xlsx': 'report_file',
  }.entries) {
    test('${sample.key} 使用表单直传、确认并保留下载签名', () async {
      final file =
          await File('${temp.path}/${sample.key}').writeAsBytes([1, 2, 3]);
      final apiAdapter = RecordingAdapter((options) {
        if (options.path.endsWith('/presign')) {
          expect(options.data['purpose'], sample.value);
          expect(options.data['size_bytes'], 3);
          return jsonResponse({
            'method': 'POST',
            'upload_url': signedPost,
            'object_key': 'u/1/file',
            'fields': {'key': 'u/1/file', 'policy': 'signed-policy'}
          });
        }
        expect(options.path.endsWith('/uploads/confirm'), isTrue);
        expect(
            options.data, {'object_key': 'u/1/file', 'purpose': sample.value});
        return jsonResponse({'verified': true, 'download_url': signedGet});
      });
      final uploadAdapter = RecordingAdapter((options) {
        expect(options.uri.toString(), signedPost);
        expect(options.headers.containsKey('Authorization'), isFalse);
        expect(options.contentType, startsWith('multipart/form-data'));
        final form = options.data as FormData;
        expect(Map.fromEntries(form.fields),
            {'key': 'u/1/file', 'policy': 'signed-policy'});
        expect(form.files.single.key, 'file');
        expect(form.files.single.value.filename, sample.key);
        return jsonResponse({});
      });
      final apiDio = Dio(BaseOptions(
          baseUrl: 'https://api.example',
          headers: {'Authorization': 'Bearer api-only'}))
        ..httpClientAdapter = apiAdapter;
      final storageDio = Dio()..httpClientAdapter = uploadAdapter;
      expect(await ToolApi(apiDio, uploadDio: storageDio).uploadFile(file.path),
          signedGet);
      expect(apiAdapter.requests.length, 2);
      expect(uploadAdapter.requests.single.method, 'POST');
    });
  }

  test('PDF 沿用知识库入口，PUT 后确认解析且保留签名', () async {
    final file = await File('${temp.path}/notes.pdf').writeAsBytes([1]);
    final apiAdapter = RecordingAdapter((options) {
      if (options.path.endsWith('/upload-url')) {
        expect(options.data['filename'], 'notes.pdf');
        expect(options.data['size'], 1);
        return jsonResponse({
          'upload_url': signedPut,
          'document_id': 'doc-1',
          'file_url': signedGet
        });
      }
      expect(options.path.endsWith('/kb/documents/doc-1/confirm'), isTrue);
      return jsonResponse({'status': 'processing'}, status: 202);
    });
    final uploads = RecordingAdapter((_) => jsonResponse({}));
    final api = ToolApi(
        Dio(BaseOptions(baseUrl: 'https://api.example'))
          ..httpClientAdapter = apiAdapter,
        uploadDio: Dio()..httpClientAdapter = uploads);
    expect(await api.uploadFile(file.path), signedGet);
    expect(apiAdapter.requests.length, 2);
    expect(uploads.requests.length, 1);
  });

  test('直传失败不确认或返回伪下载地址', () async {
    final file = await File('${temp.path}/photo.png').writeAsBytes([1]);
    final apiAdapter = RecordingAdapter((_) => jsonResponse({
          'method': 'POST',
          'upload_url': signedPost,
          'object_key': 'u/1/file',
          'fields': {'key': 'u/1/file'}
        }));
    final uploads = RecordingAdapter((_) => jsonResponse({}, status: 403));
    final api = ToolApi(
        Dio(BaseOptions(baseUrl: 'https://api.example'))
          ..httpClientAdapter = apiAdapter,
        uploadDio: Dio()..httpClientAdapter = uploads);
    await expectLater(api.uploadFile(file.path), throwsA(isA<DioException>()));
    expect(apiAdapter.requests.length, 1);
  });

  for (final name in [
    'certificate.pdf',
    'certificate.jpg',
    'certificate.png'
  ]) {
    test('$name 认证材料走独立上传并确认，不创建知识库文档', () async {
      final file = await File('${temp.path}/$name').writeAsBytes([1]);
      final apiAdapter = RecordingAdapter((options) {
        if (options.path.endsWith('/presign')) {
          expect(options.data['purpose'], 'certification_file');
          return jsonResponse({
            'method': 'POST',
            'upload_url': signedPost,
            'object_key': 'u/1/file',
            'fields': {'key': 'u/1/file'}
          });
        }
        expect(options.path.endsWith('/uploads/confirm'), isTrue);
        expect(options.data,
            {'object_key': 'u/1/file', 'purpose': 'certification_file'});
        return jsonResponse({'download_url': signedGet});
      });
      final uploads = RecordingAdapter((options) {
        expect(options.method, 'POST');
        return jsonResponse({});
      });
      final api = ToolApi(
          Dio(BaseOptions(baseUrl: 'https://api.example'))
            ..httpClientAdapter = apiAdapter,
          uploadDio: Dio()..httpClientAdapter = uploads);
      expect(
          await api.uploadFile(file.path, certificationFile: true), signedGet);
      expect(apiAdapter.requests.length, 2);
      expect(uploads.requests.length, 1);
    });
  }

  test('未提供下载URL时不回退到去掉签名的私有桶地址', () async {
    final file = await File('${temp.path}/photo.png').writeAsBytes([1]);
    final apiAdapter = RecordingAdapter(
        (options) => jsonResponse(options.path.endsWith('/presign')
            ? {
                'method': 'POST',
                'upload_url': signedPost,
                'object_key': 'u/1/file',
                'fields': {'key': 'u/1/file'}
              }
            : {'verified': true}));
    final api = ToolApi(
        Dio(BaseOptions(baseUrl: 'https://api.example'))
          ..httpClientAdapter = apiAdapter,
        uploadDio: Dio()
          ..httpClientAdapter = RecordingAdapter((_) => jsonResponse({})));
    await expectLater(api.uploadFile(file.path), throwsA(isA<DioException>()));
  });

  test('表单直传缺少签名字段时拒绝上传和确认', () async {
    final file = await File('${temp.path}/photo.png').writeAsBytes([1]);
    final apiAdapter = RecordingAdapter((_) => jsonResponse({
          'method': 'POST',
          'upload_url': signedPost,
          'object_key': 'u/1/file'
        }));
    final uploadAdapter = RecordingAdapter((_) => throw StateError('不应上传'));
    final api = ToolApi(
        Dio(BaseOptions(baseUrl: 'https://api.example'))
          ..httpClientAdapter = apiAdapter,
        uploadDio: Dio()..httpClientAdapter = uploadAdapter);
    await expectLater(api.uploadFile(file.path), throwsA(isA<DioException>()));
    expect(apiAdapter.requests.length, 1);
    expect(uploadAdapter.requests, isEmpty);
  });

  test('不支持、空和超限文件在请求前拒绝', () async {
    final apiAdapter = RecordingAdapter((_) => throw StateError('不应请求网络'));
    final api = ToolApi(Dio()..httpClientAdapter = apiAdapter);
    final unsupported = await File('${temp.path}/script.exe').writeAsBytes([1]);
    final legacySpreadsheet =
        await File('${temp.path}/scores.xls').writeAsBytes([1]);
    final empty = await File('${temp.path}/empty.jpg').create();
    final large =
        await File('${temp.path}/large.jpg').open(mode: FileMode.write);
    await large.truncate(50 * 1024 * 1024 + 1);
    await large.close();
    for (final path in [
      unsupported.path,
      legacySpreadsheet.path,
      empty.path,
      '${temp.path}/large.jpg'
    ]) {
      await expectLater(api.uploadFile(path), throwsA(isA<DioException>()));
    }
    expect(apiAdapter.requests, isEmpty);
  });

  for (final extension in ['csv', 'xlsx']) {
    test('$extension 成绩文件超过 5 MiB 时不请求上传地址', () async {
      final apiAdapter = RecordingAdapter((_) => throw StateError('不应请求网络'));
      final api = ToolApi(Dio()..httpClientAdapter = apiAdapter);
      final path = '${temp.path}/scores.$extension';
      final file = await File(path).open(mode: FileMode.write);
      await file.truncate(5 * 1024 * 1024 + 1);
      await file.close();
      await expectLater(
          api.uploadFile(path),
          throwsA(isA<DioException>()
              .having((error) => error.error, 'error', '成绩文件不能超过 5 MiB')));
      expect(apiAdapter.requests, isEmpty);
    });
  }
}
