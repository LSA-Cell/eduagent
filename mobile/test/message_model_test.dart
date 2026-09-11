import 'package:eduagent_mobile/models/message.dart';
import 'package:eduagent_mobile/models/tool_result.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('ChatMessage JSON', () {
    test('toJson/fromJson 往返一致', () {
      final msg = ChatMessage(
        id: 'm1',
        conversationId: 'c1',
        role: MessageRole.assistant,
        contentType: MessageContentType.text,
        content: '你好',
        createdAt: DateTime.utc(2026, 1, 1, 8),
        status: MessageStatus.sent,
      );
      final restored = ChatMessage.fromJson(msg.toJson());
      expect(restored.id, msg.id);
      expect(restored.conversationId, msg.conversationId);
      expect(restored.role, msg.role);
      expect(restored.contentType, msg.contentType);
      expect(restored.content, msg.content);
      expect(restored.createdAt, msg.createdAt);
      expect(restored.status, msg.status);
    });

    test('未知枚举值回退默认值', () {
      final msg = ChatMessage.fromJson({
        'id': 'm2',
        'role': 'alien',
        'contentType': 'hologram',
        'status': 'lost',
      });
      expect(msg.role, MessageRole.user);
      expect(msg.contentType, MessageContentType.text);
      expect(msg.status, MessageStatus.sent);
    });

    test('工具结果字段随消息往返', () {
      final msg = ChatMessage(
        id: 'm3',
        conversationId: 'c1',
        role: MessageRole.assistant,
        contentType: MessageContentType.toolResult,
        content: '',
        toolResult: const {
          'imageUrl': 'https://example.com/hw.jpg',
          'score': 88,
          'summary': '整体不错',
          'annotations': [
            {'x': 0.5, 'y': 0.3, 'comment': '计算错误', 'severity': 'error'},
          ],
        },
        createdAt: DateTime.utc(2026),
      );
      final restored = ChatMessage.fromJson(msg.toJson());
      expect(restored.toolResult, isNotNull);
      final grading = GradingResult.fromJson(restored.toolResult!);
      expect(grading.score, 88);
      expect(grading.annotations.single.comment, '计算错误');
    });

    test('copyWith 追加流式 chunk', () {
      final msg = ChatMessage(
        id: 'm4',
        conversationId: 'c1',
        role: MessageRole.assistant,
        contentType: MessageContentType.text,
        content: '你',
        createdAt: DateTime.utc(2026),
        status: MessageStatus.streaming,
      );
      final appended = msg.copyWith(content: '${msg.content}好');
      expect(appended.content, '你好');
      expect(appended.status, MessageStatus.streaming);
      expect(appended.id, msg.id);
    });
  });

  group('GradingResult', () {
    test('坐标与批注解析', () {
      final r = GradingResult.fromJson(const {
        'imageUrl': 'u',
        'score': 92.5,
        'summary': 's',
        'annotations': [
          {'x': 0.1, 'y': 0.2, 'radius': 0.08, 'comment': '好', 'severity': 'praise'},
        ],
      });
      expect(r.annotations.single.x, closeTo(0.1, 1e-9));
      expect(r.annotations.single.radius, closeTo(0.08, 1e-9));
      expect(r.annotations.single.severity, 'praise');
    });
  });
}
