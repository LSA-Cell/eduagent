/// 工具结果相关模型（批改 / PPT / 教案 / 试卷预览）

/// 批改圈注：后端返回的归一化坐标（0~1，相对图片宽高）
class GradingAnnotation {
  const GradingAnnotation({
    required this.x,
    required this.y,
    required this.comment,
    this.radius = 0.06,
    this.severity = 'error',
  });

  final double x;
  final double y;

  /// 圈注半径（相对图片宽度）
  final double radius;

  /// 批注内容
  final String comment;

  /// error / warn / praise
  final String severity;

  factory GradingAnnotation.fromJson(Map<String, dynamic> json) =>
      GradingAnnotation(
        x: (json['x'] as num?)?.toDouble() ?? 0,
        y: (json['y'] as num?)?.toDouble() ?? 0,
        radius: (json['radius'] as num?)?.toDouble() ?? 0.06,
        comment: json['comment'] as String? ?? '',
        severity: json['severity'] as String? ?? 'error',
      );

  Map<String, dynamic> toJson() =>
      {'x': x, 'y': y, 'radius': radius, 'comment': comment, 'severity': severity};
}

/// 作业批改结果
class GradingResult {
  const GradingResult({
    required this.imageUrl,
    required this.score,
    required this.summary,
    this.annotations = const [],
  });

  final String imageUrl;
  final double score;
  final String summary;
  final List<GradingAnnotation> annotations;

  factory GradingResult.fromJson(Map<String, dynamic> json) => GradingResult(
        imageUrl: json['imageUrl'] as String? ?? '',
        score: (json['score'] as num?)?.toDouble() ?? 0,
        summary: json['summary'] as String? ?? '',
        annotations: (json['annotations'] as List<dynamic>?)
                ?.map((e) => GradingAnnotation.fromJson(e as Map<String, dynamic>))
                .toList() ??
            const [],
      );

  Map<String, dynamic> toJson() => {
        'imageUrl': imageUrl,
        'score': score,
        'summary': summary,
        'annotations': annotations.map((e) => e.toJson()).toList(),
      };
}

/// PPT 单页缩略图
class PptSlide {
  const PptSlide({required this.index, required this.thumbnailUrl, this.title});

  final int index;
  final String thumbnailUrl;
  final String? title;

  factory PptSlide.fromJson(Map<String, dynamic> json) => PptSlide(
        index: json['index'] as int? ?? 0,
        thumbnailUrl: json['thumbnailUrl'] as String? ?? '',
        title: json['title'] as String?,
      );

  Map<String, dynamic> toJson() =>
      {'index': index, 'thumbnailUrl': thumbnailUrl, if (title != null) 'title': title};
}

/// PPT 预览
class PptPreview {
  const PptPreview({
    required this.title,
    required this.slides,
    required this.downloadUrl,
  });

  final String title;
  final List<PptSlide> slides;

  /// 导出下载地址（url_launcher 打开）
  final String downloadUrl;

  factory PptPreview.fromJson(Map<String, dynamic> json) => PptPreview(
        title: json['title'] as String? ?? '未命名课件',
        slides: (json['slides'] as List<dynamic>?)
                ?.map((e) => PptSlide.fromJson(e as Map<String, dynamic>))
                .toList() ??
            const [],
        downloadUrl: json['downloadUrl'] as String? ?? '',
      );

  Map<String, dynamic> toJson() => {
        'title': title,
        'slides': slides.map((e) => e.toJson()).toList(),
        'downloadUrl': downloadUrl,
      };
}

/// 教案章节
class LessonPlanSection {
  const LessonPlanSection({required this.title, required this.content});

  final String title;
  final String content;

  factory LessonPlanSection.fromJson(Map<String, dynamic> json) =>
      LessonPlanSection(
        title: json['title'] as String? ?? '',
        content: json['content'] as String? ?? '',
      );

  Map<String, dynamic> toJson() => {'title': title, 'content': content};
}

/// 教案预览
class LessonPlanPreview {
  const LessonPlanPreview({
    required this.title,
    required this.sections,
    this.downloadUrl,
  });

  final String title;
  final List<LessonPlanSection> sections;
  final String? downloadUrl;

  factory LessonPlanPreview.fromJson(Map<String, dynamic> json) =>
      LessonPlanPreview(
        title: json['title'] as String? ?? '未命名教案',
        sections: (json['sections'] as List<dynamic>?)
                ?.map((e) => LessonPlanSection.fromJson(e as Map<String, dynamic>))
                .toList() ??
            const [],
        downloadUrl: json['downloadUrl'] as String?,
      );

  Map<String, dynamic> toJson() => {
        'title': title,
        'sections': sections.map((e) => e.toJson()).toList(),
        if (downloadUrl != null) 'downloadUrl': downloadUrl,
      };
}

/// 试卷题目
class ExamQuestion {
  const ExamQuestion({
    required this.index,
    required this.stem,
    this.options = const [],
    required this.answer,
    this.analysis,
    this.score = 5,
  });

  final int index;
  final String stem;

  /// 选择题选项；主观题为空
  final List<String> options;
  final String answer;
  final String? analysis;
  final double score;

  factory ExamQuestion.fromJson(Map<String, dynamic> json) => ExamQuestion(
        index: json['index'] as int? ?? 0,
        stem: json['stem'] as String? ?? '',
        options:
            (json['options'] as List<dynamic>?)?.map((e) => e as String).toList() ??
                const [],
        answer: json['answer'] as String? ?? '',
        analysis: json['analysis'] as String?,
        score: (json['score'] as num?)?.toDouble() ?? 5,
      );

  Map<String, dynamic> toJson() => {
        'index': index,
        'stem': stem,
        'options': options,
        'answer': answer,
        if (analysis != null) 'analysis': analysis,
        'score': score,
      };
}

/// 试卷预览
class ExamPreview {
  const ExamPreview({
    required this.title,
    required this.questions,
    this.downloadUrl,
  });

  final String title;
  final List<ExamQuestion> questions;
  final String? downloadUrl;

  factory ExamPreview.fromJson(Map<String, dynamic> json) => ExamPreview(
        title: json['title'] as String? ?? '未命名试卷',
        questions: (json['questions'] as List<dynamic>?)
                ?.map((e) => ExamQuestion.fromJson(e as Map<String, dynamic>))
                .toList() ??
            const [],
        downloadUrl: json['downloadUrl'] as String?,
      );

  Map<String, dynamic> toJson() => {
        'title': title,
        'questions': questions.map((e) => e.toJson()).toList(),
        if (downloadUrl != null) 'downloadUrl': downloadUrl,
      };
}
