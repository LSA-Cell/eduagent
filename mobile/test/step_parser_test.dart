import 'package:eduagent_mobile/features/chat/utils/step_parser.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('parseSteps', () {
    test('纯文本返回单个普通片段', () {
      final segs = parseSteps('这是一段没有步骤的讲解');
      expect(segs.length, 1);
      expect(segs.single.isStep, isFalse);
      expect(segs.single.content, '这是一段没有步骤的讲解');
    });

    test('解析带标题与不带标题的 step，并保留前后文本顺序', () {
      const input = '先引入\n'
          '<step title="审题">读题找条件 \$a^2+b^2=c^2\$</step>\n'
          '<step>代入公式计算</step>\n'
          '综上可得结论';
      final segs = parseSteps(input);
      expect(segs.length, 4);
      expect(segs[0].isStep, isFalse);
      expect(segs[0].content, '先引入');
      expect(segs[1].isStep, isTrue);
      expect(segs[1].title, '审题');
      expect(segs[1].content, contains('a^2+b^2=c^2'));
      expect(segs[2].isStep, isTrue);
      expect(segs[2].title, isNull);
      expect(segs[2].content, '代入公式计算');
      expect(segs[3].isStep, isFalse);
      expect(segs[3].content, '综上可得结论');
    });

    test('step 内容可跨多行', () {
      const input = '<step>第一行\n第二行</step>';
      final segs = parseSteps(input);
      expect(segs.single.isStep, isTrue);
      expect(segs.single.content, '第一行\n第二行');
    });

    test('空 step 体被忽略', () {
      final segs = parseSteps('A<step></step>B');
      expect(segs.length, 2);
      expect(segs.every((s) => !s.isStep), isTrue);
    });

    test('未闭合标签按普通文本处理', () {
      expect(hasSteps('<step>没有闭合'), isFalse);
      final segs = parseSteps('<step>没有闭合');
      expect(segs.single.isStep, isFalse);
    });

    test('hasSteps 识别含标签文本', () {
      expect(hasSteps('x<step>s</step>y'), isTrue);
      expect(hasSteps('普通文本'), isFalse);
    });
  });
}
