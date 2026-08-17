import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/network/api/user_api.dart';
import '../../models/flashcard.dart';
import '../chat/widgets/markdown_math.dart';

final flashcardsProvider =
    FutureProvider.autoDispose<List<Flashcard>>((ref) async {
  return ref.read(userApiProvider).listFlashcards();
});

/// 记忆卡片页：翻转卡片 + 记得/模糊/忘记
class FlashcardsPage extends ConsumerStatefulWidget {
  const FlashcardsPage({super.key});

  @override
  ConsumerState<FlashcardsPage> createState() => _FlashcardsPageState();
}

class _FlashcardsPageState extends ConsumerState<FlashcardsPage> {
  int _index = 0;

  Future<void> _review(Flashcard card, Familiarity f) async {
    try {
      await ref.read(userApiProvider).reviewFlashcard(card.id, f);
    } catch (_) {
      // 离线时仅推进进度
    }
    setState(() => _index++);
  }

  @override
  Widget build(BuildContext context) {
    final cards = ref.watch(flashcardsProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('记忆卡片')),
      body: cards.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(child: Text('加载失败：$e')),
        data: (list) {
          if (list.isEmpty) return const _CenteredHint(icon: Icons.style, text: '暂无待复习卡片');
          if (_index >= list.length) {
            return Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Container(
                    width: 84,
                    height: 84,
                    decoration: BoxDecoration(
                      color:
                          Colors.green.withValues(alpha: 0.12),
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(Icons.check_circle_outline,
                        size: 44, color: Colors.green),
                  ),
                  const SizedBox(height: 16),
                  Text('本轮复习完成！',
                      style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: 8),
                  FilledButton.tonal(
                    onPressed: () => setState(() => _index = 0),
                    child: const Text('再来一轮'),
                  ),
                ],
              ),
            );
          }
          final card = list[_index];
          return Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              children: [
                LinearProgressIndicator(
                    value: _index / list.length,
                    minHeight: 6,
                    borderRadius: BorderRadius.circular(3)),
                const SizedBox(height: 10),
                Text('${_index + 1} / ${list.length} · ${card.deck}',
                    style: Theme.of(context).textTheme.labelSmall),
                const SizedBox(height: 24),
                Expanded(child: _FlipCard(front: card.front, back: card.back)),
                const SizedBox(height: 24),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                  children: [
                    _ReviewButton(
                        label: '忘记',
                        icon: Icons.close_rounded,
                        color: Theme.of(context).colorScheme.error,
                        onTap: () => _review(card, Familiarity.forgotten)),
                    _ReviewButton(
                        label: '模糊',
                        icon: Icons.remove_rounded,
                        color: Colors.orange,
                        onTap: () => _review(card, Familiarity.fuzzy)),
                    _ReviewButton(
                        label: '记得',
                        icon: Icons.check_rounded,
                        color: Colors.green,
                        onTap: () => _review(card, Familiarity.remembered)),
                  ],
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}

/// 可翻转卡片（点击翻面）
class _FlipCard extends StatefulWidget {
  const _FlipCard({required this.front, required this.back});

  final String front;
  final String back;

  @override
  State<_FlipCard> createState() => _FlipCardState();
}

class _FlipCardState extends State<_FlipCard>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
      vsync: this, duration: const Duration(milliseconds: 300));

  void _flip() {
    if (_controller.value > 0.5) {
      _controller.reverse();
    } else {
      _controller.forward();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: _flip,
      child: AnimatedBuilder(
        animation: _controller,
        builder: (context, _) {
          final angle = _controller.value * math.pi;
          return Transform(
            alignment: Alignment.center,
            transform: Matrix4.identity()
              ..setEntry(3, 2, 0.001) // 透视
              ..rotateY(angle),
            child: angle <= math.pi / 2
                ? _Face(text: widget.front, hint: '点击翻面查看答案', isBack: false)
                : Transform(
                    alignment: Alignment.center,
                    transform: Matrix4.identity()..rotateY(math.pi),
                    child: _Face(text: widget.back, hint: '点击查看正面', isBack: true),
                  ),
          );
        },
      ),
    );
  }
}

class _Face extends StatelessWidget {
  const _Face({required this.text, required this.hint, required this.isBack});

  final String text;
  final String hint;
  final bool isBack;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      decoration: BoxDecoration(
        // 正面白卡，背面品牌浅渐变，区分正背
        gradient: isBack ? AppTokens.tintedGradient(scheme) : null,
        color: isBack ? null : Theme.of(context).cardTheme.color,
        borderRadius: BorderRadius.circular(AppTokens.radiusXl + 4),
        border: Border.all(
            color: scheme.outlineVariant.withValues(alpha: 0.6)),
        boxShadow: AppTokens.softShadow(scheme),
      ),
      padding: const EdgeInsets.all(24),
      child: Column(
        children: [
          Align(
            alignment: Alignment.centerLeft,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
              decoration: BoxDecoration(
                color: scheme.primaryContainer.withValues(alpha: 0.5),
                borderRadius: BorderRadius.circular(999),
              ),
              child: Text(isBack ? '答案' : '问题',
                  style: TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w600,
                      color: scheme.primary)),
            ),
          ),
          Expanded(
            child: Center(
              child: SingleChildScrollView(child: MarkdownMath(data: text)),
            ),
          ),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.touch_app_outlined,
                  size: 14, color: scheme.onSurfaceVariant),
              const SizedBox(width: 4),
              Text(hint, style: Theme.of(context).textTheme.labelSmall),
            ],
          ),
        ],
      ),
    );
  }
}

class _ReviewButton extends StatelessWidget {
  const _ReviewButton(
      {required this.label,
      required this.icon,
      required this.color,
      required this.onTap});

  final String label;
  final IconData icon;
  final Color color;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return FilledButton.tonalIcon(
      onPressed: onTap,
      style: FilledButton.styleFrom(
          foregroundColor: color,
          backgroundColor: color.withValues(alpha: 0.12),
          padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12)),
      icon: Icon(icon, size: 18),
      label: Text(label),
    );
  }
}

/// 空状态：图标圆底 + 提示文案
class _CenteredHint extends StatelessWidget {
  const _CenteredHint({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 72,
            height: 72,
            decoration: BoxDecoration(
              color: scheme.primaryContainer.withValues(alpha: 0.5),
              shape: BoxShape.circle,
            ),
            child: Icon(icon, size: 34, color: scheme.primary),
          ),
          const SizedBox(height: 14),
          Text(text, style: Theme.of(context).textTheme.bodyMedium),
        ],
      ),
    );
  }
}
