import 'dart:async';
import 'dart:convert';

/// 一条 SSE 事件
class SseEvent {
  const SseEvent({this.event = 'message', required this.data});

  final String event;
  final String data;

  /// 流结束标记
  bool get isDone => data.trim() == '[DONE]';
}

/// SSE 解析器：将字节流按行解析，支持跨 chunk 的断行与多行 data。
///
/// 协议要点：
/// - `data: xxx` 追加数据行（多行 data 以 \n 连接）
/// - `event: xxx` 指定事件名，缺省为 message
/// - 空行触发事件分发
/// - 以 `:` 开头的行为注释/心跳，忽略
class SseParser {
  String _event = 'message';
  final StringBuffer _data = StringBuffer();

  Stream<SseEvent> bind(Stream<List<int>> bytes) {
    return utf8.decoder
        .bind(bytes)
        .transform(const LineSplitter())
        .transform(StreamTransformer<String, SseEvent>.fromHandlers(
          handleData: _feed,
          handleDone: (sink) {
            _flush(sink);
            sink.close();
          },
        ));
  }

  void _feed(String line, EventSink<SseEvent> sink) {
    if (line.isEmpty) {
      _flush(sink);
      return;
    }
    if (line.startsWith(':')) return; // 心跳/注释

    final idx = line.indexOf(':');
    final field = idx == -1 ? line : line.substring(0, idx);
    var value = idx == -1 ? '' : line.substring(idx + 1);
    if (value.startsWith(' ')) value = value.substring(1);

    if (field == 'event') {
      _event = value;
    } else if (field == 'data') {
      if (_data.isNotEmpty) _data.write('\n');
      _data.write(value);
    }
    // id / retry 等字段忽略
  }

  void _flush(EventSink<SseEvent> sink) {
    if (_data.isEmpty) {
      _event = 'message';
      return;
    }
    sink.add(SseEvent(event: _event, data: _data.toString()));
    _data.clear();
    _event = 'message';
  }
}
