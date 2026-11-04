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
    this.model,
    this.provider,
    this.inferenceSource = 'auto',
  });

  final String conversationId;
  final String content;
  final List<String> attachments;
  final String? tool;
  final bool webSearch;

  final String? model;
  final String? provider;
  final String inferenceSource;

  Map<String, dynamic> toJson() => {
        if (!conversationId.startsWith('local_')) 'session_id': conversationId,
        'messages': [
          {'role': 'user', 'content': _contentPayload()},
        ],
        'stream': true,
        if (model != null) 'model': model,
        if (provider != null) 'provider': provider,
        'inference_source': inferenceSource,
      };
  Object _contentPayload() {
    if (attachments.isEmpty) return content;
    return [
      {'type': 'text', 'text': content},
      for (final url in attachments) {'type': 'text', 'text': '附件：$url'},
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

final chatApiProvider =
    Provider<ChatApi>((ref) => ChatApi(ref.read(dioProvider)));
