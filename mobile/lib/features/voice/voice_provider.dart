import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

import '../../core/network/api/user_api.dart';
import '../../models/voice_report.dart';
enum VoicePhase { idle, recording, processing, done }

class VoiceState {
  const VoiceState({
    this.phase = VoicePhase.idle,
    this.scenario = 'ielts_p1',
    this.amplitude = 0,
    this.seconds = 0,
    this.report,
    this.audioPath,
    this.error,
  });

  final VoicePhase phase;
  final String scenario;

  /// 归一化振幅 0~1（驱动波形动画）
  final double amplitude;
  final int seconds;
  final VoiceReport? report;

  /// 录音文件路径（用于回放）
  final String? audioPath;
  final String? error;

  VoiceState copyWith({
    VoicePhase? phase,
    String? scenario,
    double? amplitude,
    int? seconds,
    VoiceReport? report,
    String? audioPath,
    String? error,
  }) {
    return VoiceState(
      phase: phase ?? this.phase,
      scenario: scenario ?? this.scenario,
      amplitude: amplitude ?? this.amplitude,
      seconds: seconds ?? this.seconds,
      report: report ?? this.report,
      audioPath: audioPath ?? this.audioPath,
      error: error,
    );
  }
}

class VoiceNotifier extends StateNotifier<VoiceState> {
  VoiceNotifier(this._ref) : super(const VoiceState());

  final Ref _ref;
  final AudioRecorder _recorder = AudioRecorder();
  StreamSubscription<Amplitude>? _ampSub;
  Timer? _timer;

  void selectScenario(String id) {
    if (state.phase == VoicePhase.recording) return;
    state = state.copyWith(scenario: id, report: null, error: null);
  }

  Future<void> startRecording() async {
    if (!await _recorder.hasPermission()) {
      state = state.copyWith(error: '未授予麦克风权限');
      return;
    }
    final dir = await getTemporaryDirectory();
    final path = '${dir.path}/oral_${DateTime.now().millisecondsSinceEpoch}.m4a';
    await _recorder.start(const RecordConfig(encoder: AudioEncoder.aacLc),
        path: path);

    // 振幅 → 波形动画（dBFS -60~0 归一化到 0~1）
    _ampSub = _recorder
        .onAmplitudeChanged(const Duration(milliseconds: 100))
        .listen((amp) {
      final normalized = ((amp.current + 60) / 60).clamp(0.0, 1.0).toDouble();
      state = state.copyWith(amplitude: normalized);
    });
    _timer = Timer.periodic(const Duration(seconds: 1),
        (_) => state = state.copyWith(seconds: state.seconds + 1));

    state = state.copyWith(
        phase: VoicePhase.recording,
        seconds: 0,
        report: null,
        audioPath: path,
        error: null);
  }

  /// 结束录音并上传评分
  Future<void> stopAndEvaluate() async {
    _timer?.cancel();
    await _ampSub?.cancel();
    final path = await _recorder.stop();
    state = state.copyWith(phase: VoicePhase.processing, amplitude: 0);
    final file = path ?? state.audioPath;
    if (file == null) {
      state = state.copyWith(phase: VoicePhase.idle, error: '录音失败');
      return;
    }
    try {
      // ASR 未接入：先按场景送空转写，由 LLM 兜底反馈；录音本地保留用于回放
      final report =
          await _ref.read(userApiProvider).evaluateVoice(state.scenario);
      state = state.copyWith(
          phase: VoicePhase.done, report: report, audioPath: file);
    } catch (_) {
      state = state.copyWith(phase: VoicePhase.idle, error: '评分失败，请检查网络后重试');
    }
  }

  void reset() => state = VoiceState(scenario: state.scenario);

  @override
  void dispose() {
    _timer?.cancel();
    _ampSub?.cancel();
    _recorder.dispose();
    super.dispose();
  }
}

final voiceProvider =
    StateNotifierProvider<VoiceNotifier, VoiceState>((ref) => VoiceNotifier(ref));
