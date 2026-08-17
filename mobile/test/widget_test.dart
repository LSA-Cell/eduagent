// 应用冒烟测试：验证 EduAgentApp 可在 ProviderScope 下构建。
import 'package:eduagent_mobile/core/network/dio_client.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:eduagent_mobile/main.dart';

import 'fake_dio.dart';

void main() {
  testWidgets('app builds', (WidgetTester tester) async {
    // runAsync：启动即触发的会话列表请求走真实异步区（配假网络层），
    // 避免 FakeAsync 下 Dio 请求链残留的零时长计时器导致 pending-timer 断言失败
    await tester.runAsync(() async {
      await tester.pumpWidget(ProviderScope(
        overrides: [dioProvider.overrideWithValue(buildFakeDio())],
        child: const EduAgentApp(),
      ));
      await tester.pump();
    });
    expect(find.byType(EduAgentApp), findsOneWidget);
  });
}
