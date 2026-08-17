import 'package:flutter/material.dart';

import '../../models/analytics.dart';
import '../chat/widgets/markdown_math.dart';
import 'widgets/analytics_board.dart';

/// 学情分析报告页：结论与建议 + 成绩趋势/知识点掌握/题型失分三图
class AnalyticsReportPage extends StatelessWidget {
  const AnalyticsReportPage({super.key, required this.report});

  final AnalyticsReport report;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(report.title)),
      body: AnalyticsBoard(
        data: report.data,
        header: report.summary.isEmpty
            ? null
            : Card(
                margin: const EdgeInsets.only(bottom: 12),
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Icon(Icons.summarize_outlined,
                              size: 17,
                              color: Theme.of(context).colorScheme.primary),
                          const SizedBox(width: 6),
                          Text('结论与建议',
                              style: Theme.of(context).textTheme.titleSmall),
                        ],
                      ),
                      const SizedBox(height: 10),
                      MarkdownMath(data: report.summary),
                    ],
                  ),
                ),
              ),
      ),
    );
  }
}
