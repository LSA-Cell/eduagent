import 'dart:async';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

import '../../../core/storage/local_db.dart';
import '../providers/chat_provider.dart';

/// 聊天输入框：文本 + 语音录制按钮 + 附件
class ChatInput extends ConsumerStatefulWidget {
  const ChatInput({super.key});

  @override
  ConsumerState<ChatInput> createState() => _ChatInputState();
}

class _ChatInputState extends ConsumerState<ChatInput> {
  final _controller = TextEditingController();
  final _recorder = AudioRecorder();
  bool _recording = false;
  int _recordSeconds = 0;
  Timer? _timer;
  String? _recordPath;

  @override
  void initState() {
    super.initState();
    _restoreDraft();
  }

  @override
  void dispose() {
    _controller.dispose();
    _timer?.cancel();
    _recorder.dispose();
    super.dispose();
  }

  /// 恢复当前会话草稿（Hive）
  void _restoreDraft() {
    final convId = ref.read(chatProvider).currentConversationId;
    if (convId == null) return;
    final draft = ref.read(localDbProvider).draftOf(convId);
    if (draft != null && draft.isNotEmpty) {
      _controller.text = draft;
    }
  }

  void _saveDraft() {
    final convId = ref.read(chatProvider).currentConversationId;
    if (convId == null) return;
    ref.read(localDbProvider).saveDraft(convId, _controller.text);
  }

  Future<void> _send() async {
    final text = _controller.text.trim();
    if (text.isEmpty) return;
    _controller.clear();
    _saveDraft();
    await ref.read(chatProvider.notifier).sendMessage(text);
  }

  /// 在光标处插入外部文本（公式编辑器等），并保存草稿
  void _insertAtCursor(String text) {
    final value = _controller.value;
    final sel = value.selection;
    final start = sel.isValid ? sel.start : value.text.length;
    final end = sel.isValid ? sel.end : value.text.length;
    _controller.value = TextEditingValue(
      text: value.text.replaceRange(start, end, text),
      selection: TextSelection.collapsed(offset: start + text.length),
    );
    _saveDraft();
  }

  // ---------------- 语音 ----------------

  Future<void> _toggleRecord() async {
    if (_recording) {
      _timer?.cancel();
      final path = await _recorder.stop();
      setState(() => _recording = false);
      if (path != null) {
        await ref.read(chatProvider.notifier).sendAudio(path);
      }
      return;
    }
    if (!await _recorder.hasPermission()) return;
    final dir = await getTemporaryDirectory();
    _recordPath =
        '${dir.path}/voice_${DateTime.now().millisecondsSinceEpoch}.m4a';
    await _recorder.start(const RecordConfig(encoder: AudioEncoder.aacLc),
        path: _recordPath!);
    _timer = Timer.periodic(const Duration(seconds: 1),
        (_) => setState(() => _recordSeconds++));
    setState(() {
      _recording = true;
      _recordSeconds = 0;
    });
  }

  // ---------------- 附件 ----------------

  Future<void> _showAttachSheet() async {
    await showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (sheetContext) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.photo_camera),
              title: const Text('拍照'),
              onTap: () async {
                Navigator.of(sheetContext).pop();
                final x = await ImagePicker()
                    .pickImage(source: ImageSource.camera, maxWidth: 2048);
                if (x != null) await _sendFile(x.path);
              },
            ),
            ListTile(
              leading: const Icon(Icons.photo_library),
              title: const Text('相册'),
              onTap: () async {
                Navigator.of(sheetContext).pop();
                final x = await ImagePicker()
                    .pickImage(source: ImageSource.gallery, maxWidth: 2048);
                if (x != null) await _sendFile(x.path);
              },
            ),
            ListTile(
              leading: const Icon(Icons.folder_open),
              title: const Text('文件'),
              onTap: () async {
                Navigator.of(sheetContext).pop();
                final files = await FilePicker.pickFiles();
                final path = files.isEmpty ? null : files.single.path;
                if (path != null) await _sendFile(path);
              },
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _sendFile(String path) async {
    // 附件统一走 文档解析 工具上传通道
    await ref
        .read(chatProvider.notifier)
        .invokeToolWithFile('parse_document', '文档解析', path);
  }

  @override
  Widget build(BuildContext context) {
    final isStreaming =
        ref.watch(chatProvider.select((s) => s.isStreaming));
    final scheme = Theme.of(context).colorScheme;

    // 公式编辑器等外部来源请求向输入框插入文本
    ref.listen(chatInputInsertProvider, (prev, next) {
      final text = next;
      if (text == null || text.isEmpty) return;
      _insertAtCursor(text);
      ref.read(chatInputInsertProvider.notifier).state = null;
    });
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(10, 4, 10, 10),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            _RoundIconButton(
              onPressed: _toggleRecord,
              icon: Icon(_recording ? Icons.stop : Icons.mic_none, size: 21),
              backgroundColor:
                  _recording ? scheme.errorContainer : null,
              foregroundColor: _recording ? scheme.error : null,
              tooltip: _recording ? '停止并发送' : '语音输入',
            ),
            const SizedBox(width: 4),
            _RoundIconButton(
              onPressed: _showAttachSheet,
              icon: const Icon(Icons.add, size: 22),
              tooltip: '附件',
            ),
            const SizedBox(width: 8),
            Expanded(
              child: _recording
                  ? Container(
                      height: 44,
                      alignment: Alignment.center,
                      decoration: BoxDecoration(
                        color: scheme.errorContainer.withValues(alpha: 0.6),
                        borderRadius: BorderRadius.circular(22),
                      ),
                      child: Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(Icons.fiber_manual_record,
                              size: 12, color: scheme.error),
                          const SizedBox(width: 6),
                          Text('录音中 $_recordSeconds s',
                              style: TextStyle(
                                  color: scheme.onErrorContainer,
                                  fontWeight: FontWeight.w500)),
                        ],
                      ),
                    )
                  : TextField(
                      controller: _controller,
                      minLines: 1,
                      maxLines: 4,
                      textInputAction: TextInputAction.send,
                      onChanged: (_) => _saveDraft(),
                      onSubmitted: (_) => _send(),
                      decoration: InputDecoration(
                        hintText: '输入问题…',
                        filled: true,
                        fillColor: scheme.surfaceContainerHighest
                            .withValues(alpha: 0.7),
                        contentPadding: const EdgeInsets.symmetric(
                            horizontal: 18, vertical: 11),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(24),
                          borderSide: BorderSide.none,
                        ),
                        enabledBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(24),
                          borderSide: BorderSide.none,
                        ),
                        focusedBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(24),
                          borderSide: BorderSide.none,
                        ),
                      ),
                    ),
            ),
            const SizedBox(width: 8),
            // 发送键：描边圆钮，流式生成中禁用置灰
            Tooltip(
              message: '发送',
              child: GestureDetector(
                onTap: isStreaming ? null : _send,
                child: Container(
                  width: 44,
                  height: 44,
                  decoration: BoxDecoration(
                    color: isStreaming ? scheme.surfaceContainerHighest : null,
                    shape: BoxShape.circle,
                    border: isStreaming
                        ? null
                        : Border.all(color: scheme.primary, width: 1.5),
                  ),
                  child: Icon(
                    Icons.send_rounded,
                    size: 20,
                    color: isStreaming ? scheme.outline : scheme.primary,
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 输入栏两侧的圆形功能按钮（语音/附件）
class _RoundIconButton extends StatelessWidget {
  const _RoundIconButton({
    required this.onPressed,
    required this.icon,
    required this.tooltip,
    this.backgroundColor,
    this.foregroundColor,
  });

  final VoidCallback onPressed;
  final Icon icon;
  final String tooltip;
  final Color? backgroundColor;
  final Color? foregroundColor;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Tooltip(
      message: tooltip,
      child: Material(
        color: backgroundColor ??
            scheme.surfaceContainerHighest.withValues(alpha: 0.7),
        shape: const CircleBorder(),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onPressed,
          child: SizedBox(
            width: 40,
            height: 40,
            child: IconTheme(
              data: IconThemeData(
                  size: 20, color: foregroundColor ?? scheme.onSurfaceVariant),
              child: Center(child: icon),
            ),
          ),
        ),
      ),
    );
  }
}
