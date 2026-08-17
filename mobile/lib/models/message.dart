import 'model_utils.dart';

/// 消息内容类型
enum MessageContentType {
  text,
  image,
  audio,
  file,
  toolResult,
  pptPreview,
  lessonPlanPreview,
  examPreview,
}

enum MessageRole { user, assistant, system }

enum MessageStatus { sending, streaming, sent, failed }

/// 聊天消息模型（手写 JSON 序列化，纯 Dart 便于单测）
class ChatMessage {
  const ChatMessage({
    required this.id,
    required this.conversationId,
    required this.role,
    required this.contentType,
    required this.content,
    required this.createdAt,
    this.toolResult,
    this.status = MessageStatus.sent,
  });

  final String id;
  final String conversationId;
  final MessageRole role;
  final MessageContentType contentType;

  /// 文本内容 / 附件 URL / 文件路径，按 contentType 解释
  final String content;

  /// 工具结果原始 JSON（toolResult / pptPreview / lessonPlanPreview / examPreview）
  final Map<String, dynamic>? toolResult;
  final DateTime createdAt;
  final MessageStatus status;

  bool get isUser => role == MessageRole.user;

  ChatMessage copyWith({
    String? content,
    Map<String, dynamic>? toolResult,
    MessageStatus? status,
  }) {
    return ChatMessage(
      id: id,
      conversationId: conversationId,
      role: role,
      contentType: contentType,
      content: content ?? this.content,
      toolResult: toolResult ?? this.toolResult,
      createdAt: createdAt,
      status: status ?? this.status,
    );
  }

  /// 兼容两种键名：snake_case 为后端 MessageOut（session_id/content_type/created_at），
  /// camelCase 为本地缓存（LocalDb）
  factory ChatMessage.fromJson(Map<String, dynamic> json) {
    return ChatMessage(
      id: json['id'] as String,
      conversationId:
          (json['session_id'] ?? json['conversationId']) as String? ?? '',
      role: enumByName(MessageRole.values, json['role'] as String?, MessageRole.user),
      contentType: enumByName(
          MessageContentType.values,
          (json['content_type'] ?? json['contentType']) as String?,
          MessageContentType.text),
      content: json['content'] as String? ?? '',
      toolResult: json['toolResult'] as Map<String, dynamic>?,
      createdAt: DateTime.tryParse(
              (json['created_at'] ?? json['createdAt']) as String? ?? '') ??
          DateTime.now(),
      status: enumByName(MessageStatus.values, json['status'] as String?, MessageStatus.sent),
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'conversationId': conversationId,
        'role': role.name,
        'contentType': contentType.name,
        'content': content,
        if (toolResult != null) 'toolResult': toolResult,
        'createdAt': createdAt.toIso8601String(),
        'status': status.name,
      };
}
