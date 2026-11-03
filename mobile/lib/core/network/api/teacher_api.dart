import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../models/analytics.dart';
import '../../config/app_config.dart';
import '../dio_client.dart';

/// 教师端 API：学情看板 / 学情报告 / 资源库 / 认证。
///
/// 路径与后端对齐（详见 指导/后端设计/API协议/contract.md）：
/// - 看板：GET /analytics/knowledge-mastery（知识点掌握度矩阵）
/// - 报告：POST /teacher/learning-analyses（202 异步）+ 轮询详情
/// - 资源库：GET /teacher/{lesson-plans|presentations|exam-papers}
/// - 认证：POST /users/me/teacher-certification（先经 upload-url 传文件）
class TeacherApi {
  TeacherApi(this._dio);

  final Dio _dio;

  /// 资源库 kind → 资源路径
  static const Map<String, String> _libraryPaths = {
    'lesson_plan': '/teacher/lesson-plans',
    'courseware': '/teacher/presentations',
    'exam': '/teacher/exam-papers',
  };

  /// 学情看板：知识点掌握度矩阵（按掌握度升序）。
  /// 下游返回 {user_id, matrix: [{knowledge_tag, mastery(0~1), ...}]}，
  /// 这里映射为看板模型（mastery 放大到 0~100；成绩趋势/题型分布暂无数据源）。
  Future<AnalyticsData> analytics() async {
    final resp = await _dio.get<Map<String, dynamic>>(
        '${AppConfig.apiPrefix}/analytics/knowledge-mastery');
    final matrix = resp.data?['matrix'] as List<dynamic>? ?? const [];
    return AnalyticsData(
      scoreTrend: const [],
      typeDist: const [],
      mastery: [
        for (final m in matrix)
          if (m is Map<String, dynamic>)
            KnowledgeMastery(
              topic: m['knowledge_tag'] as String? ?? '',
              mastery: ((m['mastery'] as num?)?.toDouble() ?? 0) * 100,
            ),
      ],
    );
  }

  /// 生成学情分析报告：成绩文件（fileUrl）与勾选错题（mistakeIds）至少其一。
  /// 202 异步入库后轮询详情，完成（status=completed/ready）返回报告，
  /// 失败/超时抛 DioException（由调用方提示重试）。
  Future<AnalyticsReport> generateReport(
      {String? fileUrl, List<String>? mistakeIds}) async {
    final hasMistakes = mistakeIds != null && mistakeIds.isNotEmpty;
    final resp = await _dio.post<Map<String, dynamic>>(
      '${AppConfig.apiPrefix}/teacher/learning-analyses',
      data: {
        'class_name': '我的班级',
        'analysis_type': hasMistakes ? 'mistake' : (fileUrl != null ? 'score' : 'overview'),
        'data_source': {
          if (fileUrl != null) 'file_url': fileUrl,
          if (hasMistakes) 'mistake_ids': mistakeIds,
        },
      },
    );
    final created = resp.data ?? const {};
    final pollPath = created['poll_url'] as String? ??
        '/teacher/learning-analyses/${created['id']}';

    // 轮询任务状态：每 2 秒一次，最多 60 秒
    for (var attempt = 0; attempt < 30; attempt++) {
      await Future<void>.delayed(const Duration(seconds: 2));
      final detail = await _dio.get<Map<String, dynamic>>(
          '${AppConfig.apiPrefix}$pollPath');
      final data = detail.data ?? const {};
      final status = data['status'] as String? ?? 'generating';
      if (status == 'completed' || status == 'ready') {
        final report = data['report'] as Map<String, dynamic>?;
        return AnalyticsReport.fromJson({
          'title': data['class_name'] ?? '学情分析报告',
          'summary': data['summary'] ?? report?['summary'] ?? '',
          'createdAt': data['created_at'],
        });
      }
      if (status == 'failed') {
        throw DioException(
          requestOptions: detail.requestOptions,
          error: data['error'] ?? '报告生成失败',
        );
      }
    }
    throw DioException(
      requestOptions: resp.requestOptions,
      error: '报告生成超时，请稍后在学情看板查看',
    );
  }

  /// 资源库列表（教案库/课件库/试卷库共用，kind: lesson_plan/courseware/exam）。
  /// 各资源详情字段不同，这里统一补 title/updatedAt 供列表渲染。
  Future<List<Map<String, dynamic>>> listLibrary(String kind) async {
    final path = _libraryPaths[kind];
    if (path == null) return const [];
    final resp =
        await _dio.get<Map<String, dynamic>>('${AppConfig.apiPrefix}$path');
    final items = resp.data?['items'] as List<dynamic>? ?? const [];
    return [
      for (final e in items)
        if (e is Map<String, dynamic>)
          {
            ...e,
            'title':
                e['title'] ?? e['topic'] ?? e['course_name'] ?? '未命名',
            'updatedAt': e['updated_at'] ?? e['created_at'] ?? '',
          },
    ];
  }

  /// 教师认证材料提交（202 受理，待审核）。
  /// fileUrl 由调用方先经 /kb/documents/upload-url 上传获得。
  Future<void> uploadCertification(String fileUrl, {String? note}) async {
    await _dio.post<void>(
      '${AppConfig.apiPrefix}/users/me/teacher-certification',
      data: {'file_url': fileUrl, if (note != null) 'note': note},
    );
  }
}

final teacherApiProvider =
    Provider<TeacherApi>((ref) => TeacherApi(ref.read(dioProvider)));
