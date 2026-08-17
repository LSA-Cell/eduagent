/// 会话（与后端 BFF SessionOut 对齐：title、subject、created_at、updated_at）
class Conversation {
  const Conversation({
    required this.id,
    required this.title,
    required this.updatedAt,
    this.messageCount = 0,
    this.subject,
    this.createdAt,
  });

  final String id;
  final String title;
  final DateTime updatedAt;
  final int messageCount;

  /// 学科（后端 SessionOut.subject，可空）
  final String? subject;

  /// 创建时间（后端 SessionOut.created_at；本地缓存未存，可空）
  final DateTime? createdAt;

  Conversation copyWith({String? title, DateTime? updatedAt, int? messageCount}) {
    return Conversation(
      id: id,
      title: title ?? this.title,
      updatedAt: updatedAt ?? this.updatedAt,
      messageCount: messageCount ?? this.messageCount,
      subject: subject,
      createdAt: createdAt,
    );
  }

  /// 兼容两种键名：snake_case 为后端 SessionOut，camelCase 为本地缓存（LocalDb）
  factory Conversation.fromJson(Map<String, dynamic> json) => Conversation(
        id: json['id'] as String,
        title: json['title'] as String? ?? '新会话',
        updatedAt: DateTime.tryParse(
                (json['updated_at'] ?? json['updatedAt']) as String? ?? '') ??
            DateTime.now(),
        messageCount: json['messageCount'] as int? ?? 0,
        subject: json['subject'] as String?,
        createdAt:
            DateTime.tryParse((json['created_at'] ?? json['createdAt']) as String? ?? ''),
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'title': title,
        'updatedAt': updatedAt.toIso8601String(),
        'messageCount': messageCount,
        if (subject != null) 'subject': subject,
      };
}
