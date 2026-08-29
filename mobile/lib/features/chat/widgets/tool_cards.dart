import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../app/theme.dart';
import '../../../models/message.dart';
import '../../../models/tool_result.dart';
import 'markdown_math.dart';

/// 工具结果卡片集合：批改 / PPT / 教案 / 试卷 / 通用工具结果
class ToolCard extends StatelessWidget {
  const ToolCard({super.key, required this.message});

  final ChatMessage message;

  @override
  Widget build(BuildContext context) {
    final json = message.toolResult;
    if (json == null) return const SizedBox.shrink();
    return switch (message.contentType) {
      MessageContentType.pptPreview =>
        PptPreviewCard(data: PptPreview.fromJson(json)),
      MessageContentType.lessonPlanPreview =>
        LessonPlanCard(data: LessonPlanPreview.fromJson(json)),
      MessageContentType.examPreview =>
        ExamPreviewCard(data: ExamPreview.fromJson(json)),
      MessageContentType.toolResult => json.containsKey('annotations')
          ? GradingCard(data: GradingResult.fromJson(json))
          : GenericToolCard(json: json),
      _ => const SizedBox.shrink(),
    };
  }
}

/// 卡片头部的浅色渐变图标徽章
class _IconBadge extends StatelessWidget {
  const _IconBadge({required this.icon});

  final IconData icon;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: 38,
      height: 38,
      decoration: BoxDecoration(
        color: scheme.primaryContainer,
        borderRadius: BorderRadius.circular(AppTokens.radiusMd),
      ),
      child: Icon(icon, size: 20, color: scheme.primary),
    );
  }
}

/// 批改结果卡：图片 + 按后端归一化坐标叠加圈注，点击弹出批注详情
class GradingCard extends StatelessWidget {
  const GradingCard({super.key, required this.data});

  final GradingResult data;

  Color _severityColor(String severity) => switch (severity) {
        'praise' => AppPalette.sage,
        'warn' => AppPalette.amber,
        _ => AppPalette.clay,
      };

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final scoreColor = data.score >= 80
        ? AppPalette.sage
        : data.score >= 60
            ? AppPalette.amber
            : scheme.error;
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          ListTile(
            leading: const _IconBadge(icon: Icons.fact_check),
            title: const Text('批改结果'),
            trailing: Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
              decoration: BoxDecoration(
                color: scoreColor.withValues(alpha: 0.12),
                borderRadius: BorderRadius.circular(999),
              ),
              child: Text('${data.score.toStringAsFixed(0)} 分',
                  style: Theme.of(context).textTheme.titleSmall?.copyWith(
                      fontWeight: FontWeight.bold, color: scoreColor)),
            ),
          ),
          const Divider(height: 1, indent: 16, endIndent: 16),
          LayoutBuilder(
            builder: (context, constraints) {
              final w = constraints.maxWidth;
              const h = 260.0;
              return SizedBox(
                width: w,
                height: h,
                child: Stack(
                  children: [
                    Positioned.fill(
                      child: Image.network(data.imageUrl, fit: BoxFit.cover,
                          errorBuilder: (_, __, ___) => const Center(
                              child: Icon(Icons.broken_image, size: 48))),
                    ),
                    // 圈注叠加层：按归一化坐标定位
                    for (var i = 0; i < data.annotations.length; i++)
                      Positioned(
                        left: (data.annotations[i].x - data.annotations[i].radius) * w,
                        top: (data.annotations[i].y - data.annotations[i].radius) * h,
                        width: data.annotations[i].radius * 2 * w,
                        height: data.annotations[i].radius * 2 * w,
                        child: GestureDetector(
                          onTap: () => _showAnnotation(context, i),
                          child: Container(
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              border: Border.all(
                                  color: _severityColor(
                                      data.annotations[i].severity),
                                  width: 2),
                            ),
                            child: Center(
                              child: Text('${i + 1}',
                                  style: TextStyle(
                                      fontSize: 11,
                                      color: _severityColor(
                                          data.annotations[i].severity),
                                      fontWeight: FontWeight.bold)),
                            ),
                          ),
                        ),
                      ),
                  ],
                ),
              );
            },
          ),
          if (data.summary.isNotEmpty)
            Padding(
              padding: const EdgeInsets.all(12),
              child: MarkdownMath(data: data.summary),
            ),
        ],
      ),
    );
  }

  void _showAnnotation(BuildContext context, int index) {
    final a = data.annotations[index];
    showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('批注 ${index + 1}'),
        content: MarkdownMath(data: a.comment),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('知道了')),
        ],
      ),
    );
  }
}

/// PPT 预览卡：横向 PageView 缩略图 + 导出按钮
class PptPreviewCard extends StatefulWidget {
  const PptPreviewCard({super.key, required this.data});

  final PptPreview data;

  @override
  State<PptPreviewCard> createState() => _PptPreviewCardState();
}

class _PptPreviewCardState extends State<PptPreviewCard> {
  final _controller = PageController(viewportFraction: 0.62);
  int _page = 0;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final slides = widget.data.slides;
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          ListTile(
            leading: const _IconBadge(icon: Icons.slideshow),
            title: Text(widget.data.title,
                maxLines: 1, overflow: TextOverflow.ellipsis),
            subtitle: Text('共 ${slides.length} 页'),
            trailing: FilledButton.tonalIcon(
              onPressed: widget.data.downloadUrl.isEmpty
                  ? null
                  : () => launchUrl(Uri.parse(widget.data.downloadUrl),
                      mode: LaunchMode.externalApplication),
              icon: const Icon(Icons.download, size: 18),
              label: const Text('导出'),
            ),
          ),
          SizedBox(
            height: 150,
            child: PageView.builder(
              controller: _controller,
              itemCount: slides.length,
              onPageChanged: (i) => setState(() => _page = i),
              itemBuilder: (context, i) => Padding(
                padding: const EdgeInsets.symmetric(horizontal: 4),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(8),
                  child: Image.network(slides[i].thumbnailUrl,
                      fit: BoxFit.cover,
                      errorBuilder: (_, __, ___) =>
                          const Center(child: Icon(Icons.image, size: 40))),
                ),
              ),
            ),
          ),
          if (slides.isNotEmpty)
            Center(
              child: Padding(
                padding: const EdgeInsets.all(8),
                child: Text('${_page + 1} / ${slides.length}',
                    style: Theme.of(context).textTheme.labelSmall),
              ),
            ),
        ],
      ),
    );
  }
}

/// 教案预览卡：章节折叠展示
class LessonPlanCard extends StatelessWidget {
  const LessonPlanCard({super.key, required this.data});

  final LessonPlanPreview data;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          ListTile(
            leading: const _IconBadge(icon: Icons.menu_book),
            title: Text(data.title,
                maxLines: 1, overflow: TextOverflow.ellipsis),
            subtitle: Text('共 ${data.sections.length} 个环节'),
            trailing: data.downloadUrl == null
                ? null
                : IconButton(
                    icon: const Icon(Icons.download),
                    onPressed: () => launchUrl(Uri.parse(data.downloadUrl!),
                        mode: LaunchMode.externalApplication),
                  ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Column(
              children: [
                for (final s in data.sections)
                  ExpansionTile(
                    tilePadding: EdgeInsets.zero,
                    title: Text(s.title,
                        style: Theme.of(context).textTheme.titleSmall),
                    children: [
                      Align(
                        alignment: Alignment.centerLeft,
                        child: Padding(
                          padding: const EdgeInsets.only(bottom: 12),
                          child: MarkdownMath(data: s.content),
                        ),
                      ),
                    ],
                  ),
              ],
            ),
          ),
          const SizedBox(height: 8),
        ],
      ),
    );
  }
}

/// 试卷预览卡：题目列表 + 显示/隐藏答案切换
class ExamPreviewCard extends StatefulWidget {
  const ExamPreviewCard({super.key, required this.data});

  final ExamPreview data;

  @override
  State<ExamPreviewCard> createState() => _ExamPreviewCardState();
}

class _ExamPreviewCardState extends State<ExamPreviewCard> {
  bool _showAnswers = false;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          ListTile(
            leading: const _IconBadge(icon: Icons.assignment),
            title: Text(widget.data.title,
                maxLines: 1, overflow: TextOverflow.ellipsis),
            subtitle: Text('共 ${widget.data.questions.length} 题'),
            trailing: TextButton.icon(
              onPressed: () => setState(() => _showAnswers = !_showAnswers),
              icon: Icon(
                  _showAnswers ? Icons.visibility_off : Icons.visibility,
                  size: 18),
              label: Text(_showAnswers ? '隐藏答案' : '显示答案'),
            ),
          ),
          const Divider(height: 1, indent: 16, endIndent: 16),
          for (final q in widget.data.questions)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  MarkdownMath(data: '${q.index}. ${q.stem}（${q.score.toStringAsFixed(0)}分）'),
                  for (final opt in q.options)
                    Padding(
                      padding: const EdgeInsets.only(left: 16, top: 2),
                      child: MarkdownMath(data: opt),
                    ),
                  if (_showAnswers)
                    Container(
                      width: double.infinity,
                      margin: const EdgeInsets.only(top: 6),
                      padding: const EdgeInsets.all(10),
                      decoration: BoxDecoration(
                        color: scheme.secondaryContainer.withValues(alpha: 0.35),
                        borderRadius: BorderRadius.circular(AppTokens.radiusMd),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('答案：${q.answer}',
                              style: const TextStyle(
                                  fontWeight: FontWeight.bold)),
                          if (q.analysis != null && q.analysis!.isNotEmpty)
                            MarkdownMath(data: '解析：${q.analysis}'),
                        ],
                      ),
                    ),
                ],
              ),
            ),
          const SizedBox(height: 8),
        ],
      ),
    );
  }
}

/// 通用工具结果卡（公式/化学方程式/知识点检测/作文辅导等）
class GenericToolCard extends StatelessWidget {
  const GenericToolCard({super.key, required this.json});

  final Map<String, dynamic> json;

  @override
  Widget build(BuildContext context) {
    final title = json['title'] as String? ?? '工具结果';
    final summary =
        json['summary'] as String? ?? const JsonEncoder.withIndent('  ').convert(json);
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 4),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              const _IconBadge(icon: Icons.build_circle),
              const SizedBox(width: 10),
              Expanded(
                child: Text(title,
                    style: Theme.of(context).textTheme.titleSmall),
              ),
            ]),
            const SizedBox(height: 10),
            MarkdownMath(data: summary),
          ],
        ),
      ),
    );
  }
}
