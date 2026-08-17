import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../models/flashcard.dart';
import '../../../models/mistake.dart';
import '../../../models/plan.dart';
import '../../../models/user.dart';
import '../../../models/voice_report.dart';
import '../../config/app_config.dart';
import '../dio_client.dart';
import '../sse_client.dart';

/// 复习反馈 → 后端 SM-2 rating 映射
const _kRatingByFamiliarity = {
  Familiarity.forgotten: 'again',
  Familiarity.fuzzy: 'fuzzy',
  Familiarity.remembered: 'good',
};

/// 用户 API：资料 / 模型 / API Key / 错题 / 卡片 / 计划 / 口语
///
/// 路径均与后端对齐（详见 docs/api/contract.md）：
/// 资料走 BFF /users/me*，业务资源经 BFF 转发到 Agent 编排服务。
class UserApi {
  UserApi(this._dio);

  final Dio _dio;

  // ---------------- 资料 ----------------

  /// 资料更新。键位映射：
  /// - mode → PUT /users/me/mode（只覆盖 mode 字段）
  /// - grade / grade_level、subjects（取首项）/ default_subject → PUT /users/me
  /// - selectedModel / nickname 等服务端无字段，仅本地保留，不下发
  Future<UserProfile> updateProfile(Map<String, dynamic> patch) async {
    String? mode;
    final body = <String, dynamic>{};
    patch.forEach((key, value) {
      switch (key) {
        case 'mode':
          mode = value as String?;
        case 'grade':
          body['grade_level'] = value;
        case 'subjects':
          final list = (value as List).cast<String>();
          if (list.isNotEmpty) body['default_subject'] = list.first;
        case 'grade_level' || 'default_subject':
          body[key] = value;
        default:
          break; // 其余键（selectedModel 等）跳过网络请求
      }
    });

    UserProfile? profile;
    if (mode != null) {
      final resp = await _dio.put<Map<String, dynamic>>(
        '${AppConfig.apiPrefix}/users/me/mode',
        data: {'mode': mode},
      );
      profile = UserProfile.fromJson(resp.data!);
    }
    if (body.isNotEmpty) {
      final resp = await _dio.put<Map<String, dynamic>>(
        '${AppConfig.apiPrefix}/users/me',
        data: body,
      );
      profile = UserProfile.fromJson(resp.data!);
    }
    // 无可下发字段时直接回读当前资料
    return profile ?? await getProfile();
  }

  /// GET /users/me
  Future<UserProfile> getProfile() async {
    final resp =
        await _dio.get<Map<String, dynamic>>('${AppConfig.apiPrefix}/users/me');
    return UserProfile.fromJson(resp.data!);
  }

  /// 可选模型列表（双轨：自有 vLLM / 用户 API）。
  /// GET /models → 网关 OpenAI 风格 {object: list, data: [{id, ...}]}，
  /// 返回时前置 'auto'（双轨路由自动选择）。
  Future<List<String>> listModels() async {
    final resp =
        await _dio.get<Map<String, dynamic>>('${AppConfig.apiPrefix}/models');
    final data = resp.data?['data'] as List<dynamic>? ?? const [];
    return [
      'auto',
      for (final m in data)
        if (m is Map<String, dynamic> && m['id'] is String) m['id'] as String,
    ];
  }

  // ---------------- API Key 管理（服务端只返回掩码） ----------------

  Future<List<ApiKeyEntry>> listApiKeys() async {
    final resp = await _dio
        .get<List<dynamic>>('${AppConfig.apiPrefix}/users/me/api-keys');
    return (resp.data ?? [])
        .map((e) => ApiKeyEntry.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// 明文经 HTTPS 提交给 BFF 加密存储，客户端本地副本只进安全存储。
  /// provider 限定 openai / deepseek / claude / gemini。
  Future<ApiKeyEntry> addApiKey(String provider, String key) async {
    final resp = await _dio.post<Map<String, dynamic>>(
      '${AppConfig.apiPrefix}/users/me/api-keys',
      data: {'provider': provider, 'api_key': key},
    );
    return ApiKeyEntry.fromJson(resp.data!);
  }

  /// 按记录 id 删除（ApiKeyEntry.id）
  Future<void> deleteApiKey(String keyId) async {
    await _dio
        .delete<void>('${AppConfig.apiPrefix}/users/me/api-keys/$keyId');
  }

  // ---------------- 错题本 ----------------

  Future<List<Mistake>> listMistakes({String? subject}) async {
    final resp = await _dio.get<Map<String, dynamic>>(
      '${AppConfig.apiPrefix}/mistakes',
      queryParameters: {if (subject != null) 'subject': subject},
    );
    final items = resp.data?['items'] as List<dynamic>? ?? const [];
    return items
        .map((e) => Mistake.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// 复习打卡（艾宾浩斯推进）：POST /mistakes/{id}/review
  Future<Mistake> reviewMistake(String id, {bool correct = true}) async {
    final resp = await _dio.post<Map<String, dynamic>>(
      '${AppConfig.apiPrefix}/mistakes/$id/review',
      data: {'correct': correct},
    );
    return Mistake.fromJson(resp.data!);
  }

  /// 变式练习：POST /mistakes/{id}/variations（SSE，逐段产出文本增量）。
  /// 事件格式：{"type":"delta","content":...} / {"type":"done"} / {"type":"error"}。
  Stream<String> mistakeVariations(String id) async* {
    final resp = await _dio.post<ResponseBody>(
      '${AppConfig.apiPrefix}/mistakes/$id/variations',
      options: Options(
        responseType: ResponseType.stream,
        headers: {'Accept': 'text/event-stream'},
        receiveTimeout: AppConfig.sseTimeout,
      ),
    );
    final stream = resp.data?.stream;
    if (stream == null) return;
    await for (final event in SseParser().bind(stream)) {
      if (event.isDone) return;
      Map<String, dynamic> payload;
      try {
        payload = jsonDecode(event.data) as Map<String, dynamic>;
      } catch (_) {
        continue;
      }
      switch (payload['type']) {
        case 'delta':
          final content = payload['content'] as String?;
          if (content != null && content.isNotEmpty) yield content;
        case 'done':
          return;
        case 'error':
          throw ApiException(payload['message'] as String? ?? '变式生成失败');
      }
    }
  }

  // ---------------- 记忆卡片 ----------------

  Future<List<Flashcard>> listFlashcards() async {
    final resp =
        await _dio.get<Map<String, dynamic>>('${AppConfig.apiPrefix}/flashcards');
    final items = resp.data?['items'] as List<dynamic>? ?? const [];
    return items
        .map((e) => Flashcard.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// 复习反馈：remembered / fuzzy / forgotten（映射为 SM-2 的 good/fuzzy/again）
  Future<void> reviewFlashcard(String id, Familiarity familiarity) async {
    await _dio.post<void>(
      '${AppConfig.apiPrefix}/flashcards/$id/review',
      data: {'rating': _kRatingByFamiliarity[familiarity] ?? 'fuzzy'},
    );
  }

  // ---------------- 学习计划 ----------------

  /// 创建学习计划（目标/学科/每日时长/截止日期）。
  /// POST /study-plans 入参 {goal_description, current_level, days}，
  /// 响应 202 {id, task_id, poll_url}（排程由 Celery 异步生成）。
  /// 这里返回占位 StudyPlan（tasks 为空），计划页 invalidate 后重拉列表，
  /// 生成完成后 generated_schedule 随列表返回。
  Future<StudyPlan> createPlan(Map<String, dynamic> payload) async {
    final goal = (payload['title'] ?? payload['goal_description']) as String;
    final subject = payload['subject'] as String? ?? '';
    final dailyMinutes = payload['dailyMinutes'] as int?;
    final deadline = DateTime.tryParse(payload['deadline'] as String? ?? '');
    final days = deadline == null
        ? (payload['days'] as int? ?? 7)
        : deadline.difference(DateTime.now()).inDays.clamp(1, 365);
    final level = [
      if (subject.isNotEmpty) subject,
      if (dailyMinutes != null) '每日 $dailyMinutes 分钟',
    ].join('，');

    final resp = await _dio.post<Map<String, dynamic>>(
      '${AppConfig.apiPrefix}/study-plans',
      data: {
        'goal_description': goal,
        'current_level': level.isEmpty ? '未填写' : level,
        'days': days,
      },
    );
    return StudyPlan(
      id: resp.data?['id'] as String? ?? '',
      title: goal,
      subject: subject,
      tasks: const [],
      deadline: deadline,
    );
  }

  Future<List<StudyPlan>> listPlans() async {
    final resp = await _dio
        .get<Map<String, dynamic>>('${AppConfig.apiPrefix}/study-plans');
    final items = resp.data?['items'] as List<dynamic>? ?? const [];
    return items
        .map((e) => StudyPlan.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// 任务打卡：taskId 即计划日程的 day（见 StudyPlan.fromJson 映射）。
  /// 后端 checkin 幂等只增（completed_days 只加不减），
  /// 取消勾选（done=false）不下发，仅本地生效。
  Future<void> updatePlanTask(String planId, String taskId, bool done) async {
    if (!done) return;
    await _dio.post<void>(
      '${AppConfig.apiPrefix}/study-plans/$planId/checkin',
      data: {'day': int.tryParse(taskId) ?? 1},
    );
  }

  // ---------------- 口语对练 ----------------

  /// 口语评分：POST /voice/evaluate {scenario, transcript}。
  /// ASR 未接入前 transcript 传空串，由 LLM 给出兜底反馈；录音文件本地保留用于回放。
  Future<VoiceReport> evaluateVoice(String scenario,
      {String transcript = ''}) async {
    final resp = await _dio.post<Map<String, dynamic>>(
      '${AppConfig.apiPrefix}/voice/evaluate',
      data: {'scenario': scenario, 'transcript': transcript},
    );
    return VoiceReport.fromJson(resp.data!);
  }
}

final userApiProvider = Provider<UserApi>((ref) => UserApi(ref.read(dioProvider)));
