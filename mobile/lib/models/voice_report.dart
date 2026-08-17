/// 口语对练场景
class VoiceScenario {
  const VoiceScenario(this.id, this.label);

  final String id;
  final String label;

  static const list = [
    VoiceScenario('ielts_p1', '雅思 Part 1'),
    VoiceScenario('ielts_p2', '雅思 Part 2'),
    VoiceScenario('ielts_p3', '雅思 Part 3'),
    VoiceScenario('toefl', '托福口语'),
    VoiceScenario('daily', '日常对话'),
    VoiceScenario('business', '商务英语'),
    VoiceScenario('roleplay', '角色扮演'),
    VoiceScenario('reading', '课文朗读纠音'),
    VoiceScenario('speech_zh', '中文演讲'),
  ];

  static String labelOf(String id) {
    for (final s in list) {
      if (s.id == id) return s.label;
    }
    return id;
  }
}

/// 口语评分报告（发音/语法/用词/流利度 四维）
class VoiceReport {
  const VoiceReport({
    required this.overall,
    required this.pronunciation,
    required this.grammar,
    required this.vocabulary,
    required this.fluency,
    this.feedback = '',
  });

  final double overall;
  final double pronunciation;
  final double grammar;
  final double vocabulary;
  final double fluency;
  final String feedback;

  factory VoiceReport.fromJson(Map<String, dynamic> json) => VoiceReport(
        overall: (json['overall'] as num?)?.toDouble() ?? 0,
        pronunciation: (json['pronunciation'] as num?)?.toDouble() ?? 0,
        grammar: (json['grammar'] as num?)?.toDouble() ?? 0,
        vocabulary: (json['vocabulary'] as num?)?.toDouble() ?? 0,
        fluency: (json['fluency'] as num?)?.toDouble() ?? 0,
        feedback: json['feedback'] as String? ?? '',
      );

  Map<String, dynamic> toJson() => {
        'overall': overall,
        'pronunciation': pronunciation,
        'grammar': grammar,
        'vocabulary': vocabulary,
        'fluency': fluency,
        'feedback': feedback,
      };
}
