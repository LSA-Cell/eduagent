import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../models/conversation.dart';
import '../../../models/message.dart';
import '../../config/app_config.dart';
import '../dio_client.dart';
import '../sse_client.dart';

/// 聊天请求参数
class ChatRequest {
  const ChatRequest({
    required this.conversationId,
    required this.content,
    this.attachments = const [],
    this.tool,
    this.webSearch = false,
  });

  final String conversationId;
  final String content;

  /// 附件 URL 列表（先经 /kb/documents/upload-url 上传）
  final List<String> attachments;

  /// 指定工具 id（如 make_ppt），为空表示普通对话。
  /// 后端 /chat/completions 暂无 tool 参数，仅作本地语义记录，不下发。
  final String? tool;

  /// 联网搜索开关。后端暂无对应参数，仅作本地开关态，不下发。
  final bool webSearch;

  /// BFF POST /chat/completions 入参（ChatCompletionRequest）：
  /// {session_id?, messages: [{role, content}], stream: true}，
  /// user_id 由 BFF 服务端注入，客户端不可伪造。
  Map<String, dynamic> toJson() => {
        // 本地离线会话 id（local_ 前缀）非 UUID，不下发
        if (!conversationId.startsWith('local_')) 'session_id': conversationId,
        'messages': [
          {'role': 'user', 'content': _contentPayload()},
        ],
        'stream': true,
      };

  /// 无附件时 content 为纯文本；有附件时按多模态分段携带
  ///（附件以 text 分段附带 URL，BFF 持久化时拼接 text 部分）。
  Object _contentPayload() {
    if (attachments.isEmpty) return content;
    return [
      {'type': 'text', 'text': content},
      for (final url in attachments)
        {'type': 'text', 'text': '附件：$url'},
    ];
  }
}

/// 聊天 API（手写 client；SSE 流式用 ResponseType.stream + SseParser 手写解析）
class ChatApi {
  ChatApi(this._dio);

  final Dio _dio;

  /// 会话列表：GET /chat/sessions → Page{items, total, page, page_size}
  Future<List<Conversation>> listConversations() async {
    final resp = await _dio
        .get<Map<String, dynamic>>('${AppConfig.apiPrefix}/chat/sessions');
    final items = resp.data?['items'] as List<dynamic>? ?? const [];
    return items
        .map((e) => Conversation.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<Conversation> createConversation(String title) async {
    final resp = await _dio.post<Map<String, dynamic>>(
      '${AppConfig.apiPrefix}/chat/sessions',
      data: {'title': title},
    );
    return Conversation.fromJson(resp.data!);
  }

  /// 消息列表：GET /chat/sessions/{id}/messages → Page{items, ...}
  Future<List<ChatMessage>> listMessages(String conversationId) async {
    final resp = await _dio.get<Map<String, dynamic>>(
        '${AppConfig.apiPrefix}/chat/sessions/$conversationId/messages');
    final items = resp.data?['items'] as List<dynamic>? ?? const [];
    return items
        .map((e) => ChatMessage.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// SSE 流式对话：上游为 OpenAI 兼容格式
  ///（data: {"choices":[{"delta":{"content":"..."}}]}，以 data: [DONE] 结束），
  /// 这里解出增量文本后产出 SseEvent，直到 [DONE]。
  Stream<SseEvent> streamChat(ChatRequest req) async* {
    final resp = await _dio.post<ResponseBody>(
      '${AppConfig.apiPrefix}/chat/completions',
      data: req.toJson(),
      options: Options(
        responseType: ResponseType.stream,
        headers: {'Accept': 'text/event-stream'},
        receiveTimeout: AppConfig.sseTimeout,
      ),
    );
    final stream = resp.data?.stream;
    if (stream == null) return;
    await for (final event in SseParser().bind(stream)) {
      if (event.isDone) {
        yield const SseEvent(data: '[DONE]');
        return;
      }
      final delta = _extractDelta(event.data);
      if (delta.isNotEmpty) yield SseEvent(data: delta);
    }
  }

  /// 从 OpenAI chunk JSON 提取 delta.content；解析失败按空增量处理
  static String _extractDelta(String data) {
    try {
      final payload = jsonDecode(data) as Map<String, dynamic>;
      final choices = payload['choices'] as List<dynamic>?;
      final delta = choices?.firstOrNull?['delta'] as Map<String, dynamic>?;
      return delta?['content'] as String? ?? '';
    } catch (_) {
      return '';
    }
  }
}

final chatApiProvider = Provider<ChatApi>((ref) => ChatApi(ref.read(dioProvider)));
