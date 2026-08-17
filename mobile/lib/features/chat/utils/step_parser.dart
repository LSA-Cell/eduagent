/// 分步讲解 <step> 标签解析（纯 Dart，便于单测）。
///
/// 形如：
/// ```
/// 引入语...
/// <step title="审题">第一步内容，可含 markdown 与 $LaTeX$</step>
/// <step>第二步内容</step>
/// 总结语...
/// ```
library;

/// 解析后的片段：普通文本或一个步骤
class StepSegment {
  const StepSegment({required this.isStep, required this.content, this.title});

  final bool isStep;

  /// 步骤标题（<step title="...">），可为空
  final String? title;
  final String content;
}

final RegExp _stepPattern =
    RegExp(r'<step(?:\s+title="([^"]*)")?\s*>([\s\S]*?)</step>');

bool hasSteps(String text) => _stepPattern.hasMatch(text);

/// 将含 <step> 标签的文本拆分为 普通文本/步骤 有序片段
List<StepSegment> parseSteps(String text) {
  final segments = <StepSegment>[];
  var cursor = 0;
  for (final match in _stepPattern.allMatches(text)) {
    final before = text.substring(cursor, match.start).trim();
    if (before.isNotEmpty) {
      segments.add(StepSegment(isStep: false, content: before));
    }
    final body = (match.group(2) ?? '').trim();
    if (body.isNotEmpty) {
      segments.add(StepSegment(
          isStep: true, title: match.group(1), content: body));
    }
    cursor = match.end;
  }
  final tail = text.substring(cursor).trim();
  if (tail.isNotEmpty) {
    segments.add(StepSegment(isStep: false, content: tail));
  }
  return segments;
}
