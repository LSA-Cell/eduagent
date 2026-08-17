/// 学情看板数据模型

/// 成绩趋势点
class ScorePoint {
  const ScorePoint({required this.label, required this.value});

  /// 如 "第1次" / 日期
  final String label;

  /// 分数
  final double value;

  factory ScorePoint.fromJson(Map<String, dynamic> json) => ScorePoint(
        label: json['label'] as String? ?? '',
        value: (json['value'] as num?)?.toDouble() ?? 0,
      );
}

/// 知识点掌握度
class KnowledgeMastery {
  const KnowledgeMastery({required this.topic, required this.mastery});

  final String topic;

  /// 0~100
  final double mastery;

  factory KnowledgeMastery.fromJson(Map<String, dynamic> json) =>
      KnowledgeMastery(
        topic: json['topic'] as String? ?? '',
        mastery: (json['mastery'] as num?)?.toDouble() ?? 0,
      );
}

/// 题型失分占比
class TypeSlice {
  const TypeSlice({required this.type, required this.ratio});

  final String type;

  /// 占比 0~1
  final double ratio;

  factory TypeSlice.fromJson(Map<String, dynamic> json) => TypeSlice(
        type: json['type'] as String? ?? '',
        ratio: (json['ratio'] as num?)?.toDouble() ?? 0,
      );
}

/// 学情看板聚合数据
class AnalyticsData {
  const AnalyticsData({
    required this.scoreTrend,
    required this.mastery,
    required this.typeDist,
    this.studentCount = 0,
    this.avgScore = 0,
  });

  final List<ScorePoint> scoreTrend;
  final List<KnowledgeMastery> mastery;
  final List<TypeSlice> typeDist;
  final int studentCount;
  final double avgScore;

  factory AnalyticsData.fromJson(Map<String, dynamic> json) => AnalyticsData(
        scoreTrend: (json['scoreTrend'] as List<dynamic>?)
                ?.map((e) => ScorePoint.fromJson(e as Map<String, dynamic>))
                .toList() ??
            const [],
        mastery: (json['mastery'] as List<dynamic>?)
                ?.map((e) => KnowledgeMastery.fromJson(e as Map<String, dynamic>))
                .toList() ??
            const [],
        typeDist: (json['typeDist'] as List<dynamic>?)
                ?.map((e) => TypeSlice.fromJson(e as Map<String, dynamic>))
                .toList() ??
            const [],
        studentCount: json['studentCount'] as int? ?? 0,
        avgScore: (json['avgScore'] as num?)?.toDouble() ?? 0,
      );
}

/// 学情分析报告：聚合看板数据 + 结论与教学建议
class AnalyticsReport {
  const AnalyticsReport({
    required this.title,
    required this.summary,
    required this.data,
    this.createdAt,
  });

  final String title;

  /// 结论与教学建议（markdown，可含 LaTeX）
  final String summary;

  /// 图表聚合数据（复用学情看板渲染）
  final AnalyticsData data;
  final DateTime? createdAt;

  factory AnalyticsReport.fromJson(Map<String, dynamic> json) =>
      AnalyticsReport(
        title: json['title'] as String? ?? '学情分析报告',
        summary: json['summary'] as String? ?? '',
        // 后端可能把图表数据放在 data 字段，也可能平铺返回
        data: AnalyticsData.fromJson(
            (json['data'] as Map<String, dynamic>?) ?? json),
        createdAt: DateTime.tryParse(json['createdAt'] as String? ?? ''),
      );
}
