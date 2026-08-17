import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'providers/chat_provider.dart';
import 'widgets/chat_input.dart';
import 'widgets/edu_toolbar.dart';
import 'widgets/message_list.dart';

/// 聊天主页：消息列表 + 教育工具栏 + 输入框
class ChatPage extends ConsumerStatefulWidget {
  const ChatPage({super.key});

  @override
  ConsumerState<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends ConsumerState<ChatPage> {
  final _scrollController = ScrollController();

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final chatState = ref.watch(chatProvider);

    // 新消息/流式 chunk 到达时滚到底部（reverse 列表的 0 位置）
    ref.listen(chatProvider.select((s) => s.messages.length), (_, __) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(0,
            duration: const Duration(milliseconds: 200), curve: Curves.easeOut);
      }
    });

    final error = chatState.error;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 26,
              height: 26,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                border: Border.all(color: scheme.primary, width: 1.5),
              ),
              child: Icon(Icons.school, size: 15, color: scheme.primary),
            ),
            const SizedBox(width: 8),
            const Text('对话'),
          ],
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.add_comment_outlined),
            tooltip: '新会话',
            onPressed: () => ref.read(chatProvider.notifier).newConversation(),
          ),
        ],
      ),
      body: Column(
        children: [
          if (error != null)
            MaterialBanner(
              content: Text(error),
              backgroundColor:
                  Theme.of(context).colorScheme.errorContainer,
              actions: [
                TextButton(
                  onPressed: () =>
                      ref.read(chatProvider.notifier).clearError(),
                  child: const Text('知道了'),
                ),
              ],
            ),
          Expanded(
            child: MessageList(
                messages: chatState.messages, controller: _scrollController),
          ),
          const EduToolbar(),
          const ChatInput(),
        ],
      ),
    );
  }
}
