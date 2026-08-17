import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter_math_fork/flutter_math.dart';
import 'package:markdown/markdown.dart' as md;

/// Markdown + LaTeX 渲染：支持行内 $...$ 与块级 $$...$$
class MarkdownMath extends StatelessWidget {
  const MarkdownMath({super.key, required this.data, this.textStyle});

  final String data;
  final TextStyle? textStyle;

  @override
  Widget build(BuildContext context) {
    final sheet = MarkdownStyleSheet.fromTheme(Theme.of(context)).copyWith(
      p: textStyle ?? Theme.of(context).textTheme.bodyMedium,
    );
    return MarkdownBody(
      data: data,
      selectable: true,
      styleSheet: sheet,
      inlineSyntaxes: [_MathInlineSyntax()],
      builders: {'math': _MathBuilder()},
    );
  }
}

/// 将 $...$ / $$...$$ 识别为 math 内联节点
class _MathInlineSyntax extends md.InlineSyntax {
  _MathInlineSyntax() : super(r'\$\$([\s\S]+?)\$\$|\$([^\$\n]+?)\$');

  @override
  bool onMatch(md.InlineParser parser, Match match) {
    final expr = match[1] ?? match[2] ?? '';
    parser.addNode(md.Element.text('math', expr));
    return true;
  }
}

class _MathBuilder extends MarkdownElementBuilder {
  @override
  Widget? visitElementAfter(md.Element element, TextStyle? preferredStyle) {
    final expr = element.textContent;
    return Math.tex(
      expr,
      mathStyle: MathStyle.text,
      textStyle: preferredStyle,
      onErrorFallback: (_) => Text(expr), // 解析失败降级为原文
    );
  }
}
