import 'package:flutter/material.dart';
import 'package:just_audio/just_audio.dart';
import 'package:photo_view/photo_view.dart';

import '../../../app/theme.dart';
import '../../../models/message.dart';
import '../utils/step_parser.dart';
import 'markdown_math.dart';
import 'step_card.dart';
import 'tool_cards.dart';

/// 消息气泡：按 contentType 渲染 文本/markdown+latex/图片/语音/文件/工具卡片
class MessageBubble extends StatelessWidget {
  const MessageBubble({super.key, required this.message});

  final ChatMessage message;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    // 系统消息：居中灰条
    if (message.role == MessageRole.system) {
      return Center(
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 8),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
          decoration: BoxDecoration(
            color: scheme.surfaceContainerHighest.withValues(alpha: 0.7),
            borderRadius: BorderRadius.circular(999),
          ),
          child: Text(message.content,
              style: Theme.of(context).textTheme.labelSmall),
        ),
      );
    }

    final isUser = message.isUser;
    // 用户气泡用品牌渐变 + 白字；助手气泡用卡片白 + 描边
    final contentColor = isUser ? Colors.white : scheme.onSurface;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
      child: Row(
        mainAxisAlignment:
            isUser ? MainAxisAlignment.end : MainAxisAlignment.start,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (!isUser) ...[
            _AssistantAvatar(scheme: scheme),
            const SizedBox(width: 8),
          ],
          Flexible(
            child: Container(
              constraints: BoxConstraints(
                  maxWidth: MediaQuery.of(context).size.width * 0.78),
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
              decoration: BoxDecoration(
                gradient: isUser ? AppTokens.brandGradient(scheme) : null,
                color: isUser
                    ? null
                    : (Theme.of(context).cardTheme.color ??
                        scheme.surfaceContainerLowest),
                borderRadius: BorderRadius.only(
                  topLeft: const Radius.circular(AppTokens.radiusXl - 2),
                  topRight: const Radius.circular(AppTokens.radiusXl - 2),
                  bottomLeft:
                      Radius.circular(isUser ? AppTokens.radiusXl - 2 : 6),
                  bottomRight:
                      Radius.circular(isUser ? 6 : AppTokens.radiusXl - 2),
                ),
                border: isUser
                    ? null
                    : Border.all(
                        color: scheme.outlineVariant.withValues(alpha: 0.5)),
                boxShadow: isUser
                    ? [
                        BoxShadow(
                          color: scheme.primary.withValues(alpha: 0.18),
                          blurRadius: 10,
                          offset: const Offset(0, 3),
                        ),
                      ]
                    : AppTokens.softShadow(scheme),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  _buildContent(context, contentColor),
                  if (message.status == MessageStatus.failed)
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(Icons.error_outline,
                              size: 13,
                              color: isUser ? Colors.white70 : scheme.error),
                          const SizedBox(width: 4),
                          Text('发送失败',
                              style: TextStyle(
                                  fontSize: 11,
                                  color:
                                      isUser ? Colors.white70 : scheme.error)),
                        ],
                      ),
                    ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildContent(BuildContext context, Color contentColor) {
    final textStyle =
        Theme.of(context).textTheme.bodyMedium?.copyWith(color: contentColor);
    switch (message.contentType) {
      case MessageContentType.image:
        return _ImageContent(url: message.content);
      case MessageContentType.audio:
        return _AudioContent(url: message.content, textColor: contentColor);
      case MessageContentType.file:
        return Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.attach_file,
                size: 18, color: contentColor.withValues(alpha: 0.8)),
            const SizedBox(width: 6),
            Flexible(
                child: Text(message.content,
                    maxLines: 2, style: TextStyle(color: contentColor))),
          ],
        );
      case MessageContentType.toolResult:
      case MessageContentType.pptPreview:
      case MessageContentType.lessonPlanPreview:
      case MessageContentType.examPreview:
        return ToolCard(message: message);
      case MessageContentType.text:
        // assistant 文本可能含 <step> 分步讲解标签
        if (!message.isUser && hasSteps(message.content)) {
          final segments = parseSteps(message.content);
          var stepIndex = 0;
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              for (final seg in segments)
                seg.isStep
                    ? StepCard(
                        index: ++stepIndex,
                        title: seg.title,
                        content: seg.content)
                    : Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        child:
                            MarkdownMath(data: seg.content, textStyle: textStyle)),
            ],
          );
        }
        if (message.status == MessageStatus.streaming &&
            message.content.isEmpty) {
          return SizedBox(
              width: 16,
              height: 16,
              child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: message.isUser
                      ? Colors.white
                      : Theme.of(context).colorScheme.primary));
        }
        return MarkdownMath(data: message.content, textStyle: textStyle);
    }
  }
}

/// 助手头像：品牌渐变圆 + 校园图标
class _AssistantAvatar extends StatelessWidget {
  const _AssistantAvatar({required this.scheme});

  final ColorScheme scheme;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 30,
      height: 30,
      margin: const EdgeInsets.only(top: 2),
      decoration: BoxDecoration(
        gradient: AppTokens.brandGradient(scheme),
        shape: BoxShape.circle,
      ),
      child: const Icon(Icons.school, size: 17, color: Colors.white),
    );
  }
}

/// 图片消息：点击全屏查看（photo_view 缩放）
class _ImageContent extends StatelessWidget {
  const _ImageContent({required this.url});

  final String url;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => showDialog<void>(
        context: context,
        builder: (_) => Dialog.fullscreen(
          child: Stack(
            children: [
              PhotoView(imageProvider: NetworkImage(url)),
              SafeArea(
                child: Align(
                  alignment: Alignment.topRight,
                  child: IconButton(
                    icon: const Icon(Icons.close, color: Colors.white),
                    onPressed: () => Navigator.of(context).pop(),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(AppTokens.radiusMd),
        child: Image.network(url,
            height: 160,
            fit: BoxFit.cover,
            errorBuilder: (_, __, ___) =>
                const Icon(Icons.broken_image, size: 40)),
      ),
    );
  }
}

/// 语音消息：播放/暂停
class _AudioContent extends StatefulWidget {
  const _AudioContent({required this.url, required this.textColor});

  final String url;
  final Color textColor;

  @override
  State<_AudioContent> createState() => _AudioContentState();
}

class _AudioContentState extends State<_AudioContent> {
  late final AudioPlayer _player;
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    _player = AudioPlayer();
  }

  @override
  void dispose() {
    _player.dispose();
    super.dispose();
  }

  Future<void> _toggle() async {
    if (_player.playing) {
      await _player.pause();
      return;
    }
    setState(() => _loading = true);
    try {
      if (_player.audioSource == null) {
        await _player.setUrl(widget.url);
      }
      await _player.play();
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return StreamBuilder<bool>(
      stream: _player.playingStream,
      builder: (context, snapshot) {
        final playing = snapshot.data ?? false;
        return Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            IconButton(
              onPressed: _loading ? null : _toggle,
              color: widget.textColor,
              icon: _loading
                  ? SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(
                          strokeWidth: 2, color: widget.textColor))
                  : Icon(playing ? Icons.pause_circle : Icons.play_circle),
            ),
            Text('语音', style: TextStyle(color: widget.textColor)),
          ],
        );
      },
    );
  }
}
