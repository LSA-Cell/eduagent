/// 错题本条目
class Mistake {
  const Mistake({
    required this.id,
    required this.subject,
    required this.question,
    required this.myAnswer,
    required this.correctAnswer,
    required this.createdAt,
    this.analysis = '',
    this.mastery = 0,
  });

  final String id;
  final String subject;
  final String question;
  final String myAnswer;
  final String correctAnswer;
  final String analysis;

  /// 掌握度 0~100
  final int mastery;
  final DateTime createdAt;

  /// 兼容后端 Agent mistake_to_dict（wrong_answer/correct_answer/created_at）
  factory Mistake.fromJson(Map<String, dynamic> json) => Mistake(
        id: json['id'] as String,
        subject: json['subject'] as String? ?? '未分类',
        question: json['question'] as String? ?? '',
        myAnswer: (json['wrong_answer'] ?? json['myAnswer']) as String? ?? '',
        correctAnswer:
            (json['correct_answer'] ?? json['correctAnswer']) as String? ?? '',
        analysis: json['analysis'] as String? ?? '',
        mastery: (json['mastery'] as num?)?.toInt() ?? 0,
        createdAt: DateTime.tryParse(
                (json['created_at'] ?? json['createdAt']) as String? ?? '') ??
            DateTime.now(),
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'subject': subject,
        'question': question,
        'myAnswer': myAnswer,
        'correctAnswer': correctAnswer,
        'analysis': analysis,
        'mastery': mastery,
        'createdAt': createdAt.toIso8601String(),
      };
}
