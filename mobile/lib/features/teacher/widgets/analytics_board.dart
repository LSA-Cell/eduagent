import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import '../../../app/theme.dart';
import '../../../models/analytics.dart';

/// 学情看板：成绩趋势线图 + 知识点掌握柱状图 + 题型失分饼图
class AnalyticsBoard extends StatelessWidget {
  const AnalyticsBoard({super.key, required this.data, this.header});

  final AnalyticsData data;

  /// 可选头部内容（报告页传入“结论与建议”卡片）
  final Widget? header;

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(12),
      children: [
        if (header != null) header!,
        _Section(
          title: '成绩趋势（班级平均 ${data.avgScore.toStringAsFixed(1)} 分）',
          icon: Icons.show_chart,
          height: 200,
          child: _ScoreTrendChart(points: data.scoreTrend),
        ),
        _Section(
          title: '知识点掌握度',
          icon: Icons.bar_chart_rounded,
          height: 200,
          child: _MasteryBarChart(items: data.mastery),
        ),
        _Section(
          title: '题型失分占比',
          icon: Icons.pie_chart_outline,
          height: 220,
          child: _TypePieChart(slices: data.typeDist),
        ),
      ],
    );
  }
}

class _Section extends StatelessWidget {
  const _Section(
      {required this.title,
      required this.icon,
      required this.height,
      required this.child});

  final String title;
  final IconData icon;
  final double height;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(icon, size: 17, color: scheme.primary),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(title,
                      style: Theme.of(context).textTheme.titleSmall),
                ),
              ],
            ),
            const SizedBox(height: 14),
            SizedBox(height: height, child: child),
          ],
        ),
      ),
    );
  }
}

class _ScoreTrendChart extends StatelessWidget {
  const _ScoreTrendChart({required this.points});

  final List<ScorePoint> points;

  @override
  Widget build(BuildContext context) {
    if (points.isEmpty) return const Center(child: Text('暂无数据'));
    return LineChart(
      LineChartData(
        gridData: const FlGridData(show: false),
        borderData: FlBorderData(show: false),
        titlesData: FlTitlesData(
          topTitles:
              const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          rightTitles:
              const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              interval: 1,
              getTitlesWidget: (value, meta) {
                final i = value.toInt();
                if (i < 0 || i >= points.length) return const SizedBox.shrink();
                return Text(points[i].label,
                    style: const TextStyle(fontSize: 10));
              },
            ),
          ),
        ),
        lineBarsData: [
          LineChartBarData(
            isCurved: true,
            isStrokeCapRound: true,
            barWidth: 3.5,
            color: Theme.of(context).colorScheme.primary,
            dotData: FlDotData(
              show: true,
              getDotPainter: (spot, percent, bar, index) =>
                  FlDotCirclePainter(
                radius: 3.5,
                color: Theme.of(context).colorScheme.primary,
                strokeWidth: 2,
                strokeColor:
                    Theme.of(context).cardTheme.color ?? AppPalette.cardLight,
              ),
            ),
            belowBarData: BarAreaData(
              show: true,
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [
                  Theme.of(context)
                      .colorScheme
                      .primary
                      .withValues(alpha: 0.22),
                  Theme.of(context)
                      .colorScheme
                      .primary
                      .withValues(alpha: 0.02),
                ],
              ),
            ),
            spots: [
              for (var i = 0; i < points.length; i++)
                FlSpot(i.toDouble(), points[i].value),
            ],
          ),
        ],
      ),
    );
  }
}

class _MasteryBarChart extends StatelessWidget {
  const _MasteryBarChart({required this.items});

  final List<KnowledgeMastery> items;

  @override
  Widget build(BuildContext context) {
    if (items.isEmpty) return const Center(child: Text('暂无数据'));
    return BarChart(
      BarChartData(
        gridData: const FlGridData(show: false),
        borderData: FlBorderData(show: false),
        titlesData: FlTitlesData(
          topTitles:
              const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          rightTitles:
              const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              getTitlesWidget: (value, meta) {
                final i = value.toInt();
                if (i < 0 || i >= items.length) return const SizedBox.shrink();
                return Text(items[i].topic,
                    style: const TextStyle(fontSize: 10));
              },
            ),
          ),
        ),
        barGroups: [
          for (var i = 0; i < items.length; i++)
            BarChartGroupData(
              x: i,
              barRods: [
                BarChartRodData(
                  toY: items[i].mastery,
                  width: 20,
                  borderRadius: const BorderRadius.vertical(
                      top: Radius.circular(6)),
                  gradient: LinearGradient(
                    begin: Alignment.bottomCenter,
                    end: Alignment.topCenter,
                    colors: () {
                      final c = items[i].mastery >= 80
                          ? AppPalette.sage
                          : items[i].mastery >= 60
                              ? AppPalette.amber
                              : Theme.of(context).colorScheme.error;
                      return [c.withValues(alpha: 0.65), c];
                    }(),
                  ),
                ),
              ],
            ),
        ],
      ),
    );
  }
}

class _TypePieChart extends StatelessWidget {
  const _TypePieChart({required this.slices});

  final List<TypeSlice> slices;

  static const _colors = [
    AppPalette.terracotta,
    AppPalette.sage,
    AppPalette.amber,
    Color(0xFF6B7A8C),
    Color(0xFF8C6B7A),
  ];

  @override
  Widget build(BuildContext context) {
    if (slices.isEmpty) return const Center(child: Text('暂无数据'));
    return PieChart(
      PieChartData(
        sectionsSpace: 3,
        centerSpaceRadius: 44,
        sections: [
          for (var i = 0; i < slices.length; i++)
            PieChartSectionData(
              value: slices[i].ratio,
              color: _colors[i % _colors.length],
              radius: 68,
              borderSide:
                  BorderSide(color: Theme.of(context).cardTheme.color ?? AppPalette.cardLight, width: 2),
              title:
                  '${slices[i].type}\n${(slices[i].ratio * 100).toStringAsFixed(0)}%',
              titleStyle: const TextStyle(
                  fontSize: 10,
                  fontWeight: FontWeight.w600,
                  color: Colors.white),
            ),
        ],
      ),
    );
  }
}
