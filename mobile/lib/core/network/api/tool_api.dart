import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../config/app_config.dart';
import '../dio_client.dart';

/// 工具/文件 API：附件上传（预签名二阶段）与异步生成任务（PPT/教案/试卷）。
///
/// 生成任务直接映射真实资源路径（BFF 转发 Agent 编排，教师身份限制）：
/// - make_ppt         ↔ /teacher/presentations
/// - make_lesson_plan ↔ /teacher/lesson-plans
/// - make_exam        ↔ /teacher/exam-papers
/// 创建返回 202 {id, task_id, poll_url}；轮询 GET poll_url，
/// 详情含 status（generating/ready|completed/failed）与 file_url/预览 JSON。
class ToolApi {
  ToolApi(this._dio, {Dio? uploadDio}) : _uploadDio = uploadDio ?? Dio();

  final Dio _dio;
  // 存储直传不使用带用户鉴权拦截器的 API client。
  final Dio _uploadDio;

  /// 生成类工具 → 资源路径
  static const Map<String, String> generationPaths = {
    'make_ppt': '/teacher/presentations',
    'make_lesson_plan': '/teacher/lesson-plans',
    'make_exam': '/teacher/exam-papers',
  };

  /// 生成成功终态（后端：presentations→ready，lesson/exam→completed）
  static const Set<String> successStatuses = {
    'ready',
    'completed',
    'succeeded'
  };

  /// 图片、音频、成绩文件和认证材料走表单直传，知识库文档走 PUT。
  Future<String> uploadFile(String path,
      {bool certificationFile = false,
      void Function(int sent, int total)? onProgress}) async {
    final fileName = path.split('/').last;
    final extension = fileName.split('.').last.toLowerCase();
    const contentTypes = {
      'jpg': 'image/jpeg',
      'jpeg': 'image/jpeg',
      'png': 'image/png',
      'webp': 'image/webp',
      'm4a': 'audio/mp4',
      'wav': 'audio/wav',
      'mp3': 'audio/mpeg',
      'ogg': 'audio/ogg',
      'csv': 'text/csv',
      'xlsx':
          'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'pdf': 'application/pdf',
      'docx':
          'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'txt': 'text/plain',
      'md': 'text/markdown',
    };
    final contentType = contentTypes[extension];
    final file = File(path);
    final size = await file.length();
    final isReportFile = extension == 'csv' || extension == 'xlsx';
    if (isReportFile && size > 5 * 1024 * 1024) {
      throw DioException(
        requestOptions: RequestOptions(path: path),
        error: '成绩文件不能超过 5 MiB',
      );
    }
    if (contentType == null ||
        size <= 0 ||
        size > 50 * 1024 * 1024 ||
        (certificationFile && !{'pdf', 'jpg', 'png'}.contains(extension))) {
      throw DioException(
        requestOptions: RequestOptions(path: path),
        error: '文件类型不支持、为空或超过 50MB',
      );
    }
    final String? purpose = certificationFile
        ? 'certification_file'
        : contentType.startsWith('image/')
            ? 'chat_image'
            : contentType.startsWith('audio/')
                ? 'chat_audio'
                : isReportFile
                    ? 'report_file'
                    : null;
    final resp = await _dio.post<Map<String, dynamic>>(
      '${AppConfig.apiPrefix}${purpose == null ? '/kb/documents/upload-url' : '/uploads/presign'}',
      data: purpose == null
          ? {'filename': fileName, 'content_type': contentType, 'size': size}
          : {
              'file_name': fileName,
              'content_type': contentType,
              'size_bytes': size,
              'purpose': purpose
            },
    );
    final data = resp.data ?? const {};
    final uploadUrl = data['upload_url'] as String? ?? '';
    final objectKey = data['object_key'] as String? ?? '';
    final documentId = data['document_id'] as String? ?? '';
    if (uploadUrl.isEmpty ||
        (purpose == null ? documentId.isEmpty : objectKey.isEmpty)) {
      throw DioException(
        requestOptions: resp.requestOptions,
        error: '上传响应缺少预签名地址或文件标识',
      );
    }
    if (purpose == null) {
      await _uploadDio.put<void>(
        uploadUrl,
        data: await file.readAsBytes(),
        options: Options(headers: {
          'Content-Type': contentType,
          ...?data['headers'] as Map<String, dynamic>?
        }),
        onSendProgress: onProgress,
      );
    } else {
      final fields = data['fields'];
      if (data['method'] != 'POST' || fields is! Map<String, dynamic>) {
        throw DioException(
          requestOptions: resp.requestOptions,
          error: '上传响应缺少表单字段',
        );
      }
      await _uploadDio.post<void>(
        uploadUrl,
        data: FormData.fromMap({
          ...fields,
          'file': await MultipartFile.fromFile(path, filename: fileName),
        }),
        onSendProgress: onProgress,
      );
    }
    final confirmed = await _dio.post<Map<String, dynamic>>(
      '${AppConfig.apiPrefix}${purpose == null ? '/kb/documents/$documentId/confirm' : '/uploads/confirm'}',
      data: purpose == null
          ? null
          : {'object_key': objectKey, 'purpose': purpose},
    );
    final url = (purpose == null
            ? data['file_url']
            : confirmed.data?['download_url']) as String? ??
        '';
    if (url.isEmpty) {
      throw DioException(
        requestOptions: confirmed.requestOptions,
        error: '上传确认缺少下载地址',
      );
    }
    return url;
  }

  /// 创建异步生成任务（PPT/教案/试卷走 docgen + Celery），返回轮询路径（poll_url）
  Future<String> createGenerationTask(
      String toolId, Map<String, dynamic> payload) async {
    final path = generationPaths[toolId];
    if (path == null) {
      throw ArgumentError('非生成类工具: $toolId');
    }
    final resp = await _dio.post<Map<String, dynamic>>(
      '${AppConfig.apiPrefix}$path',
      data: _buildCreatePayload(toolId, payload),
    );
    final data = resp.data ?? const {};
    return data['poll_url'] as String? ?? '$path/${data['id']}';
  }

  /// 查询生成任务状态：pollPath 即创建时返回的 poll_url（如 /teacher/presentations/{id}）。
  /// 响应为资源详情：status + file_url/预览 JSON。
  Future<Map<String, dynamic>> getGenerationTask(String pollPath) async {
    final resp =
        await _dio.get<Map<String, dynamic>>('${AppConfig.apiPrefix}$pollPath');
    return resp.data ?? const {};
  }

  /// 表单参数 → 后端创建入参（Agent schemas 的 *CreateRequest）
  Map<String, dynamic> _buildCreatePayload(
      String toolId, Map<String, dynamic> p) {
    switch (toolId) {
      case 'make_ppt':
        return {
          'topic': p['topic'],
          'grade': p['grade'] ?? '高中',
          'subject': p['subject'] ?? '通用',
          'style': p['style'] ?? 'default',
          'pages': p['pageCount'] ?? 10,
        };
      case 'make_lesson_plan':
        return {
          'course_name': p['topic'],
          'grade': p['grade'] ?? '高中',
          // 课时数 → 分钟数（45 分钟/课时）
          'duration': ((p['classHours'] as num?)?.toInt() ?? 1) * 45,
        };
      case 'make_exam':
        return {
          'grade': p['grade'] ?? '高中',
          'subject': p['subject'] ?? '数学',
          'question_types': p['questionTypes'] ?? const ['选择题', '填空题', '解答题'],
          'difficulty': _difficultyOf(p['difficulty'] as String?),
          'count': p['questionCount'] ?? 10,
        };
      default:
        return p;
    }
  }

  static String _difficultyOf(String? label) => switch (label) {
        '简单' => 'easy',
        '困难' => 'hard',
        _ => 'medium',
      };

  /// 资源详情 JSON → 聊天预览卡片 JSON（models/tool_result.dart 的 *Preview 结构）
  static Map<String, dynamic> previewOf(
      String toolId, Map<String, dynamic> detail) {
    switch (toolId) {
      case 'make_ppt':
        final slides = detail['slides'] as List<dynamic>? ?? const [];
        final thumbs = detail['thumbnail_urls'] as List<dynamic>? ?? const [];
        return {
          'title': detail['topic'] ?? detail['title'] ?? '未命名课件',
          'downloadUrl': detail['file_url'] ?? '',
          'slides': [
            for (var i = 0; i < slides.length; i++)
              {
                'index': i,
                'thumbnailUrl': i < thumbs.length ? thumbs[i] : '',
                'title': slides[i] is Map<String, dynamic>
                    ? (slides[i] as Map<String, dynamic>)['title']
                    : '$slides[i]',
              },
          ],
        };
      case 'make_lesson_plan':
        final content = detail['content'] as Map<String, dynamic>? ?? const {};
        return {
          'title': detail['course_name'] ?? detail['title'] ?? '未命名教案',
          'downloadUrl': detail['file_url'],
          'sections': [
            for (final e in content.entries)
              {
                'title': e.key,
                'content': e.value is String ? e.value : '${e.value}',
              },
          ],
        };
      case 'make_exam':
        return {
          'title': detail['title'] ?? '未命名试卷',
          'downloadUrl':
              detail['student_file_url'] ?? detail['teacher_file_url'],
          'questions': detail['questions'] ?? const [],
        };
      default:
        return detail;
    }
  }
}

final toolApiProvider =
    Provider<ToolApi>((ref) => ToolApi(ref.read(dioProvider)));
