import 'package:flutter/material.dart';

import '../../../models/message.dart';
import 'message_bubble.dart';

/// 消息列表：reverse ListView.builder，关闭 keepAlive 支撑万级消息滚动
class MessageList extends StatelessWidget {
  const MessageList({super.key, required this.messages, this.controller});

  final List<ChatMessage> messages;
  final ScrollController? controller;

  @override
  Widget build(BuildContext context) {
    if (messages.isEmpty) {
      final scheme = Theme.of(context).colorScheme;
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 76,
              height: 76,
              decoration: BoxDecoration(
                color: scheme.primaryContainer.withValues(alpha: 0.5),
                shape: BoxShape.circle,
              ),
              child: Icon(Icons.forum_outlined,
                  size: 36, color: scheme.primary),
            ),
            const SizedBox(height: 16),
            Text('开始提问吧',
                style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 6),
            Text('比如"帮我讲解勾股定理"',
                style: Theme.of(context).textTheme.bodySmall),
          ],
        ),
      );
    }
    return ListView.builder(
      controller: controller,
      reverse: true, // 最新在底部
      addAutomaticKeepAlives: false, // 虚拟滚动：不保留屏幕外元素
      addRepaintBoundaries: true,
      itemCount: messages.length,
      itemBuilder: (context, index) {
        final message = messages[messages.length - 1 - index];
        return MessageBubble(key: ValueKey(message.id), message: message);
      },
    );
  }
}
