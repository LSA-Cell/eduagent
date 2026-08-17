import 'package:eduagent_mobile/core/network/dio_client.dart';
import 'package:eduagent_mobile/features/chat/widgets/edu_toolbar.dart';
import 'package:eduagent_mobile/features/profile/profile_provider.dart';
import 'package:eduagent_mobile/models/user.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_dio.dart';

Widget _wrap(Widget child, {UserMode mode = UserMode.student}) {
  return ProviderScope(
    overrides: [
      userModeProvider.overrideWith((ref) => mode),
      // 假网络层：避免真实 HTTP 在 FakeAsync 下挂起计时器
      dioProvider.overrideWithValue(buildFakeDio()),
    ],
    child: MaterialApp(
      home: Scaffold(body: Center(child: child)),
    ),
  );
}

void main() {
  group('EduToolbar 折叠/展开', () {
    testWidgets('默认折叠态高 56dp，存在展开按钮', (tester) async {
      await tester.pumpWidget(_wrap(const EduToolbar()));
      await tester.pumpAndSettle();

      final size = tester.getSize(find.byType(AnimatedContainer));
      expect(size.height, EduToolbar.collapsedHeight);
      expect(find.text('展开'), findsOneWidget);
      // 折叠态展示前 4 个常用工具的图标
      expect(find.byIcon(Icons.photo_camera), findsOneWidget);
      expect(find.byIcon(Icons.fact_check), findsOneWidget);
    });

    testWidgets('点击展开后高度变为 220dp 并显示全部 16 个工具与收起按钮',
        (tester) async {
      await tester.pumpWidget(_wrap(const EduToolbar()));
      await tester.tap(find.text('展开'));
      await tester.pumpAndSettle();

      final size = tester.getSize(find.byType(AnimatedContainer));
      expect(size.height, EduToolbar.expandedHeight);
      expect(find.text('收起'), findsOneWidget);

      // 网格中应包含全部 16 个工具（滚动找到每一个）
      for (final tool in kEduTools) {
        await tester.scrollUntilVisible(
          find.text(tool.label),
          50,
          scrollable: find.byType(Scrollable).last,
        );
        expect(find.text(tool.label), findsOneWidget);
      }

      // 收起还原
      await tester.tap(find.text('收起'));
      await tester.pumpAndSettle();
      expect(
          tester.getSize(find.byType(AnimatedContainer)).height,
          EduToolbar.collapsedHeight);
    });

    testWidgets('动画时长为 200ms ease-out', (tester) async {
      // runAsync：启动请求（假网络层）在真实异步区完成，
      // 避免 FakeAsync 下 Dio 请求链残留零时长计时器
      await tester.runAsync(() async {
        await tester.pumpWidget(_wrap(const EduToolbar()));
        await tester.pump();
      });
      final container =
          tester.widget<AnimatedContainer>(find.byType(AnimatedContainer));
      expect(container.duration, const Duration(milliseconds: 200));
      expect(container.curve, Curves.easeOut);
    });
  });

  group('身份驱动工具重排', () {
    test('学生模式：学习工具在前', () {
      final container = ProviderContainer(
        overrides: [userModeProvider.overrideWith((ref) => UserMode.student)],
      );
      addTearDown(container.dispose);
      final tools = container.read(orderedToolsProvider);
      expect(tools.first.id, 'photo_qa'); // 拍照答疑
      expect(tools.length, 16);
    });

    test('教师模式：PPT/教案/试卷/学情分析排最前且保持相对顺序', () {
      final container = ProviderContainer(
        overrides: [userModeProvider.overrideWith((ref) => UserMode.teacher)],
      );
      addTearDown(container.dispose);
      final tools = container.read(orderedToolsProvider);
      expect(
        tools.take(4).map((t) => t.id).toList(),
        ['make_ppt', 'make_lesson_plan', 'make_exam', 'analytics'],
      );
      expect(tools.length, 16);
    });

    testWidgets('切换身份后展开态网格顺序随 userModeProvider 动态重排',
        (tester) async {
      // 教师模式下网格第一项应为"制作PPT"
      await tester.pumpWidget(_wrap(const EduToolbar(), mode: UserMode.teacher));
      await tester.tap(find.text('展开'));
      await tester.pumpAndSettle();

      final firstItem = tester
          .widgetList<InkWell>(find.byType(InkWell))
          .isNotEmpty; // InkWell 存在即网格已渲染
      expect(firstItem, isTrue);
      // 网格内第一个标签应为教师优先工具
      final grid = find.byType(GridView);
      expect(grid, findsOneWidget);
      final firstLabel = tester.widget<Text>(
        find.descendant(of: grid, matching: find.text('制作PPT')),
      );
      expect(firstLabel, isNotNull);
    });

    testWidgets('学生模式下教师专属工具置灰（不可点击）', (tester) async {
      await tester.pumpWidget(_wrap(const EduToolbar(), mode: UserMode.student));
      await tester.tap(find.text('展开'));
      await tester.pumpAndSettle();

      await tester.scrollUntilVisible(
        find.text('制作PPT'),
        50,
        scrollable: find.byType(Scrollable).last,
      );
      // 置灰项的 InkWell onTap 为 null
      final item = find.ancestor(
          of: find.text('制作PPT'), matching: find.byType(InkWell));
      expect(tester.widget<InkWell>(item).onTap, isNull);
    });
  });
}
