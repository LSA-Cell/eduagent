import 'package:flutter/material.dart';

import '../../../app/theme.dart';
import 'markdown_math.dart';

/// 分步讲解卡片：序号 + 标题 + 可展开折叠的内容
class StepCard extends StatefulWidget {
  const StepCard({
    super.key,
    required this.index,
    required this.content,
    this.title,
    this.initialExpanded = true,
  });

  /// 步骤序号（从 1 开始）
  final int index;
  final String? title;
  final String content;
  final bool initialExpanded;

  @override
  State<StepCard> createState() => _StepCardState();
}

class _StepCardState extends State<StepCard> {
  late bool _expanded = widget.initialExpanded;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 4),
      elevation: 0,
      color: scheme.primaryContainer.withValues(alpha: 0.28),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppTokens.radiusMd),
        side: BorderSide(color: scheme.primary.withValues(alpha: 0.12)),
      ),
      child: InkWell(
        onTap: () => setState(() => _expanded = !_expanded),
        borderRadius: BorderRadius.circular(AppTokens.radiusMd),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Container(
                    width: 22,
                    height: 22,
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(7),
                      border: Border.all(color: scheme.primary, width: 1.2),
                    ),
                    alignment: Alignment.center,
                    child: Text('${widget.index}',
                        style: TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w700,
                            color: scheme.primary)),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      widget.title ?? '第 ${widget.index} 步',
                      style: Theme.of(context)
                          .textTheme
                          .titleSmall
                          ?.copyWith(fontWeight: FontWeight.bold),
                    ),
                  ),
                  Icon(
                    _expanded ? Icons.expand_less : Icons.expand_more,
                    size: 20,
                    color: scheme.primary,
                  ),
                ],
              ),
              AnimatedCrossFade(
                duration: const Duration(milliseconds: 150),
                crossFadeState: _expanded
                    ? CrossFadeState.showFirst
                    : CrossFadeState.showSecond,
                firstChild: Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: MarkdownMath(data: widget.content),
                ),
                secondChild: const SizedBox.shrink(),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
