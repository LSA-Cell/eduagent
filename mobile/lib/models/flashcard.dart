import 'model_utils.dart';

/// 记忆熟悉度
enum Familiarity { forgotten, fuzzy, remembered }

/// 记忆卡片
class Flashcard {
  const Flashcard({
    required this.id,
    required this.front,
    required this.back,
    required this.deck,
    required this.nextReview,
  });

  final String id;
  final String front;
  final String back;

  /// 卡组（如 "英语单词" / "历史年表"）
  final String deck;

  /// 下次复习时间
  final DateTime nextReview;

  /// 兼容后端 Agent flashcard_to_dict（due_date 为下次复习时间）
  factory Flashcard.fromJson(Map<String, dynamic> json) => Flashcard(
        id: json['id'] as String,
        front: json['front'] as String? ?? '',
        back: json['back'] as String? ?? '',
        deck: json['deck'] as String? ?? '默认',
        nextReview: DateTime.tryParse(
                (json['due_date'] ?? json['nextReview']) as String? ?? '') ??
            DateTime.now(),
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'front': front,
        'back': back,
        'deck': deck,
        'nextReview': nextReview.toIso8601String(),
      };
}

/// 供熟悉度序列化复用（避免未使用告警的显式引用）
Familiarity familiarityFromName(String? name) =>
    enumByName(Familiarity.values, name, Familiarity.fuzzy);
