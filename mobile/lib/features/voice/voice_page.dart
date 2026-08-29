import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:just_audio/just_audio.dart';

import '../../app/theme.dart';
import '../../models/voice_report.dart';
import 'voice_provider.dart';

/// 口语对练全屏页：场景选择 + 波形动画 + 四维评分报告
class VoicePage extends ConsumerStatefulWidget {
  const VoicePage({super.key});

  @override
  ConsumerState<VoicePage> createState() => _VoicePageState();
}

class _VoicePageState extends ConsumerState<VoicePage>
    with SingleTickerProviderStateMixin {
  late final AnimationController _waveController;

  @override
  void initState() {
    super.initState();
    _waveController = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 1200))
      ..repeat();
  }

  @override
  void dispose() {
    _waveController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(voiceProvider);
    final notifier = ref.read(voiceProvider.notifier);

    return Scaffold(
      appBar: AppBar(title: const Text('口语对练')),
      body: SafeArea(
        child: Column(
          children: [
            // 场景选择
            SizedBox(
              height: 48,
              child: ListView(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.symmetric(horizontal: 12),
                children: [
                  for (final s in VoiceScenario.list)
                    Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: ChoiceChip(
                        label: Text(s.label),
                        selected: state.scenario == s.id,
                        onSelected: (_) => notifier.selectScenario(s.id),
                      ),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 24),
            // 波形动画区
            Expanded(
              child: Center(
                child: switch (state.phase) {
                  VoicePhase.processing =>
                    const CircularProgressIndicator(),
                  VoicePhase.done when state.report != null =>
                    _ReportCard(report: state.report!, audioPath: state.audioPath),
                  _ => AnimatedBuilder(
                      animation: _waveController,
                      builder: (context, _) => CustomPaint(
                        size: const Size(double.infinity, 160),
                        painter: _WavePainter(
                          phase: _waveController.value * 2 * math.pi,
                          amplitude: state.phase == VoicePhase.recording
                              ? state.amplitude
                              : 0.05,
                          color: Theme.of(context).colorScheme.primary,
                        ),
                      ),
                    ),
                },
              ),
            ),
            if (state.error != null)
              Padding(
                padding: const EdgeInsets.all(8),
                child: Text(state.error!,
                    style:
                        TextStyle(color: Theme.of(context).colorScheme.error)),
              ),
            if (state.phase == VoicePhase.recording)
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 14, vertical: 5),
                decoration: BoxDecoration(
                  color: Theme.of(context)
                      .colorScheme
                      .errorContainer
                      .withValues(alpha: 0.5),
                  borderRadius: BorderRadius.circular(999),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.fiber_manual_record,
                        size: 11, color: Theme.of(context).colorScheme.error),
                    const SizedBox(width: 6),
                    Text('${state.seconds}s',
                        style: Theme.of(context)
                            .textTheme
                            .titleSmall
                            ?.copyWith(
                                color: Theme.of(context)
                                    .colorScheme
                                    .onErrorContainer)),
                  ],
                ),
              ),
            const SizedBox(height: 12),
            // 录音/结束按钮
            Padding(
              padding: const EdgeInsets.only(bottom: 32),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  if (state.phase == VoicePhase.done)
                    FilledButton.tonalIcon(
                      onPressed: notifier.reset,
                      icon: const Icon(Icons.refresh, size: 18),
                      label: const Text('再来一次'),
                    ),
                  const SizedBox(width: 16),
                  _MicButton(
                    recording: state.phase == VoicePhase.recording,
                    processing: state.phase == VoicePhase.processing,
                    onTap: state.phase == VoicePhase.recording
                        ? notifier.stopAndEvaluate
                        : state.phase == VoicePhase.processing
                            ? null
                            : notifier.startRecording,
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 录音主按钮：品牌渐变 + 光晕，录音中变为红色停止键并带描边环
class _MicButton extends StatelessWidget {
  const _MicButton({
    required this.recording,
    required this.processing,
    required this.onTap,
  });

  final bool recording;
  final bool processing;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: 80,
        height: 80,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          border: recording
              ? Border.all(
                  color: scheme.error.withValues(alpha: 0.35), width: 5)
              : null,
        ),
        child: Container(
          margin: recording ? null : const EdgeInsets.all(2),
          decoration: BoxDecoration(
            color: recording ? scheme.error : scheme.primary,
            shape: BoxShape.circle,
            boxShadow: processing ? null : AppTokens.softShadow(scheme),
          ),
          child: Icon(
            recording ? Icons.stop_rounded : Icons.mic,
            color: Colors.white,
            size: 34,
          ),
        ),
      ),
    );
  }
}

/// sin 波形（接 record 振幅；未录音时显示待机微波）
class _WavePainter extends CustomPainter {
  _WavePainter(
      {required this.phase, required this.amplitude, required this.color});

  final double phase;
  final double amplitude;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 2.5
      ..style = PaintingStyle.stroke;
    final path = Path();
    final midY = size.height / 2;
    for (var x = 0.0; x <= size.width; x += 2) {
      final t = x / size.width;
      // 两个频率叠加，中部振幅最大
      final envelope = math.sin(t * math.pi);
      final y = midY +
          envelope *
              amplitude *
              size.height *
              0.45 *
              math.sin(t * 4 * math.pi + phase) +
          envelope *
              amplitude *
              size.height *
              0.2 *
              math.sin(t * 9 * math.pi - phase * 1.3);
      x == 0 ? path.moveTo(x, y) : path.lineTo(x, y);
    }
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(_WavePainter old) =>
      old.phase != phase || old.amplitude != amplitude;
}

/// 评分报告卡：发音/语法/用词/流利度 四维 + 总评
class _ReportCard extends StatelessWidget {
  const _ReportCard({required this.report, this.audioPath});

  final VoiceReport report;
  final String? audioPath;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 24),
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // 总分：渐变圆环徽章
            Container(
              width: 92,
              height: 92,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: scheme.primaryContainer,
                border: Border.all(
                    color: scheme.primary.withValues(alpha: 0.25), width: 3),
              ),
              alignment: Alignment.center,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(report.overall.toStringAsFixed(1),
                      style: Theme.of(context)
                          .textTheme
                          .headlineSmall
                          ?.copyWith(
                              fontWeight: FontWeight.bold,
                              color: scheme.primary)),
                  Text('总分',
                      style: Theme.of(context).textTheme.labelSmall),
                ],
              ),
            ),
            const SizedBox(height: 20),
            _ScoreBar(label: '发音', score: report.pronunciation),
            _ScoreBar(label: '语法', score: report.grammar),
            _ScoreBar(label: '用词', score: report.vocabulary),
            _ScoreBar(label: '流利度', score: report.fluency),
            if (report.feedback.isNotEmpty) ...[
              const SizedBox(height: 14),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: scheme.surfaceContainerHighest.withValues(alpha: 0.5),
                  borderRadius: BorderRadius.circular(AppTokens.radiusMd),
                ),
                child: Text(report.feedback,
                    style: Theme.of(context).textTheme.bodySmall),
              ),
            ],
            if (audioPath != null) ...[
              const SizedBox(height: 8),
              _PlaybackButton(path: audioPath!),
            ],
          ],
        ),
      ),
    );
  }
}

class _ScoreBar extends StatelessWidget {
  const _ScoreBar({required this.label, required this.score});

  final String label;

  /// 0~9（雅思制）或 0~100，统一按 9 分制满刻度
  final double score;

  @override
  Widget build(BuildContext context) {
    final max = score > 9 ? 100.0 : 9.0;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          SizedBox(
              width: 56,
              child: Text(label, style: Theme.of(context).textTheme.bodySmall)),
          Expanded(
            child: LinearProgressIndicator(
              value: (score / max).clamp(0.0, 1.0),
              minHeight: 8,
              borderRadius: BorderRadius.circular(4),
            ),
          ),
          const SizedBox(width: 8),
          Text(score.toStringAsFixed(1),
              style: Theme.of(context).textTheme.labelMedium),
        ],
      ),
    );
  }
}

/// 回放自己的录音
class _PlaybackButton extends StatefulWidget {
  const _PlaybackButton({required this.path});

  final String path;

  @override
  State<_PlaybackButton> createState() => _PlaybackButtonState();
}

class _PlaybackButtonState extends State<_PlaybackButton> {
  late final AudioPlayer _player;

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

  @override
  Widget build(BuildContext context) {
    return StreamBuilder<bool>(
      stream: _player.playingStream,
      builder: (context, snapshot) {
        final playing = snapshot.data ?? false;
        return TextButton.icon(
          onPressed: () async {
            if (playing) {
              await _player.pause();
            } else {
              if (_player.audioSource == null) {
                await _player.setFilePath(widget.path);
              }
              await _player.play();
            }
          },
          icon: Icon(playing ? Icons.pause : Icons.replay),
          label: Text(playing ? '暂停' : '回放录音'),
        );
      },
    );
  }
}
