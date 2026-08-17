import 'dart:convert';

import 'package:eduagent_mobile/core/network/sse_client.dart';
import 'package:flutter_test/flutter_test.dart';

Stream<List<int>> _chunks(List<String> chunks) =>
    Stream.fromIterable(chunks.map(utf8.encode));

void main() {
  group('SseParser', () {
    test('解析基本 data 事件并以空行分隔', () async {
      final events =
          await SseParser().bind(_chunks(['data: hello\n\ndata: world\n\n'])).toList();
      expect(events.length, 2);
      expect(events[0].event, 'message');
      expect(events[0].data, 'hello');
      expect(events[1].data, 'world');
    });

    test('跨 chunk 断行正确拼接', () async {
      final events = await SseParser()
          .bind(_chunks(['data: 你', '好，世', '界\n\n']))
          .toList();
      expect(events.single.data, '你好，世界');
    });

    test('多行 data 以换行连接', () async {
      final events = await SseParser()
          .bind(_chunks(['data: line1\ndata: line2\n\n']))
          .toList();
      expect(events.single.data, 'line1\nline2');
    });

    test('event 字段与 [DONE] 标记', () async {
      final events = await SseParser()
          .bind(_chunks(['event: chunk\ndata: abc\n\nevent: done\ndata: [DONE]\n\n']))
          .toList();
      expect(events.length, 2);
      expect(events[0].event, 'chunk');
      expect(events[0].isDone, isFalse);
      expect(events[1].event, 'done');
      expect(events[1].isDone, isTrue);
    });

    test('心跳注释行被忽略', () async {
      final events = await SseParser()
          .bind(_chunks([': ping\n\ndata: real\n\n']))
          .toList();
      expect(events.length, 1);
      expect(events.single.data, 'real');
    });

    test('流结束时冲刷未以空行结尾的数据', () async {
      final events =
          await SseParser().bind(_chunks(['data: tail'])).toList();
      expect(events.single.data, 'tail');
    });
  });
}
