import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import '../../../core/network/api/chat_api.dart';
import '../../../core/network/api/tool_api.dart';
import '../../../core/network/api/user_api.dart';
import '../../../core/network/dio_client.dart';
import '../../../core/storage/local_db.dart';
import '../../../models/conversation.dart';
import '../../../models/message.dart';
import '../../auth/auth_provider.dart';

class ChatState {
  const ChatState({
    this.conversations = const [],
    this.currentConversationId,
    this.messages = const [],
    this.isStreaming = false,
    this.webSearchEnabled = false,
    this.error,
  });

  final List<Conversation> conversations;
  final String? currentConversationId;

  /// 当前会话消息（按时间升序，UI 层 reverse 显示）
  final List<ChatMessage> messages;

  /// 是否正在流式接收
  final bool isStreaming;

  /// 联网搜索开关（工具栏开关态高亮）
  final bool webSearchEnabled;
  final String? error;

  ChatState copyWith({
    List<Conversation>? conversations,
    String? currentConversationId,
    List<ChatMessage>? messages,
    bool? isStreaming,
    bool? webSearchEnabled,
    String? error,
  }) {
    return ChatState(
      conversations: conversations ?? this.conversations,
      currentConversationId:
          currentConversationId ?? this.currentConversationId,
      messages: messages ?? this.messages,
      isStreaming: isStreaming ?? this.isStreaming,
      webSearchEnabled: webSearchEnabled ?? this.webSearchEnabled,
      error: error,
    );
  }
}

class ChatNotifier extends StateNotifier<ChatState> {
  ChatNotifier(this._ref) : super(const ChatState()) {
    _loadLocal();
  }

  final Ref _ref;
  final _uuid = const Uuid();

  ChatApi get _chatApi => _ref.read(chatApiProvider);
  ToolApi get _toolApi => _ref.read(toolApiProvider);
  UserApi get _userApi => _ref.read(userApiProvider);
  LocalDb get _db => _ref.read(localDbProvider);

  // ---------------- 会话管理 ----------------

  Future<void> _loadLocal() async {
    // 先读本地缓存保证秒开，再拉远端刷新。
    // 本地缓存放宽处理：widget 测试等场景未 override localDbProvider 时不阻塞，
    // 直接走远端刷新（refreshConversations 自身亦有离线容错）。
    try {
      final cached = await _db.loadConversations();
      if (cached.isNotEmpty) {
        state = state.copyWith(conversations: cached);
      }
    } catch (_) {}
    await refreshConversations();
  }

  Future<void> refreshConversations() async {
    try {
      final list = await _chatApi.listConversations();
      state = state.copyWith(conversations: list);
      await _db.cacheConversations(list);
    } catch (_) {
      // 离线时保留缓存
    }
  }

  Future<void> selectConversation(String id) async {
    if (state.currentConversationId == id) return;
    state = state.copyWith(currentConversationId: id, messages: const []);
    final cached = await _db.loadMessages(id);
    if (cached.isNotEmpty) {
      state = state.copyWith(messages: cached);
    }
    try {
      final remote = await _chatApi.listMessages(id);
      state = state.copyWith(messages: remote);
      await _db.cacheMessages(id, remote);
    } catch (_) {}
  }

  void newConversation() {
    state = state.copyWith(
      currentConversationId: null,
      messages: const [],
      isStreaming: false,
    );
  }

  /// 确保有会话 id：首条消息发送前创建
  Future<String> _ensureConversation(String firstText) async {
    final existing = state.currentConversationId;
    if (existing != null) return existing;
    final title =
        firstText.length > 20 ? '${firstText.substring(0, 20)}…' : firstText;
    try {
      final conv = await _chatApi.createConversation(title);
      state = state.copyWith(
        currentConversationId: conv.id,
        conversations: [conv, ...state.conversations],
      );
      return conv.id;
    } catch (_) {
      // 离线场景：本地生成 id，待恢复后由离线队列补偿
      final localId = 'local_${_uuid.v4()}';
      final conv =
          Conversation(id: localId, title: title, updatedAt: DateTime.now());
      state = state.copyWith(
        currentConversationId: localId,
        conversations: [conv, ...state.conversations],
      );
      return localId;
    }
  }

  // ---------------- 发送与流式接收 ----------------

  /// 发送文本消息（可带附件 URL），打字机式流式接收
  Future<void> sendMessage(String text,
      {List<String> attachments = const []}) async {
    if (text.trim().isEmpty && attachments.isEmpty) return;
    if (state.isStreaming) return;

    final convId = await _ensureConversation(text);
    final now = DateTime.now();
    final userMsg = ChatMessage(
      id: _uuid.v4(),
      conversationId: convId,
      role: MessageRole.user,
      contentType: attachments.isEmpty
          ? MessageContentType.text
          : MessageContentType.file,
      content: text,
      createdAt: now,
      status: MessageStatus.sending,
    );
    final assistantMsg = ChatMessage(
      id: _uuid.v4(),
      conversationId: convId,
      role: MessageRole.assistant,
      contentType: MessageContentType.text,
      content: '',
      createdAt: now,
      status: MessageStatus.streaming,
    );
    state = state.copyWith(
      messages: [...state.messages, userMsg, assistantMsg],
      isStreaming: true,
      error: null,
    );

    final buffer = StringBuffer();
    try {
      final selectedModel =
          _ref.read(authProvider).user?.selectedModel ?? 'auto';
      final useKimi = selectedModel.startsWith('kimi-');
      final req = ChatRequest(
        conversationId: convId,
        content: text,
        attachments: attachments,
        webSearch: state.webSearchEnabled,
        model: useKimi ? selectedModel : null,
        provider: useKimi ? 'moonshot' : null,
        inferenceSource: useKimi ? 'external' : 'auto',
      );
      await for (final event in _chatApi.streamChat(req)) {
        if (event.isDone || event.event == 'done') break;
        if (event.event == 'error') {
          throw ApiException(event.data);
        }
        buffer.write(event.data);
        _appendChunk(assistantMsg.id, event.data);
      }
      _finishMessage(userMsg.id, assistantMsg.id);
    } on DioException catch (e) {
      // 网络失败：入离线队列，标记失败
      await _db.enqueueOffline(_reqPayload(convId, text, attachments));
      _failMessage(userMsg.id, assistantMsg.id, e.message ?? '网络异常');
    } on ApiException catch (e) {
      _failMessage(userMsg.id, assistantMsg.id, e.message);
    } catch (_) {
      await _db.enqueueOffline(_reqPayload(convId, text, attachments));
      _failMessage(userMsg.id, assistantMsg.id, '发送失败，已加入离线队列');
    }
  }

  Map<String, dynamic> _reqPayload(
          String convId, String text, List<String> attachments) =>
      {
        'conversationId': convId,
        'content': text,
        'attachments': attachments,
      };

  /// 将流式 chunk 追加到最后一条 assistant 消息（打字机效果）
  void _appendChunk(String messageId, String chunk) {
    final messages = state.messages;
    if (messages.isEmpty || messages.last.id != messageId) return;
    final updated =
        messages.last.copyWith(content: messages.last.content + chunk);
    state = state.copyWith(
        messages: [...messages.sublist(0, messages.length - 1), updated]);
  }

  void _finishMessage(String userId, String assistantId) {
    final messages = [
      for (final m in state.messages)
        if (m.id == userId || m.id == assistantId)
          m.copyWith(status: MessageStatus.sent)
        else
          m,
    ];
    state = state.copyWith(messages: messages, isStreaming: false);
    final convId = state.currentConversationId;
    if (convId != null) {
      _db.cacheMessages(convId, messages);
    }
  }

  void _failMessage(String userId, String assistantId, String reason) {
    final messages = [
      for (final m in state.messages)
        if (m.id == userId || m.id == assistantId)
          m.copyWith(status: MessageStatus.failed)
        else
          m,
    ];
    state =
        state.copyWith(messages: messages, isStreaming: false, error: reason);
  }

  // ---------------- 工具调用 ----------------

  /// 直接触发类工具：插入系统消息后，把工具需求作为一条用户消息走
  /// POST /chat/completions 流式对话触发（后端无独立 /tools/{id} 接口）。
  Future<void> invokeTool(String toolId, String toolLabel,
      {Map<String, dynamic> payload = const {}}) async {
    await _ensureConversation('[$toolLabel]');
    final sysMsg = ChatMessage(
      id: _uuid.v4(),
      conversationId: state.currentConversationId ?? '',
      role: MessageRole.system,
      contentType: MessageContentType.text,
      content: '正在使用 $toolLabel…',
      createdAt: DateTime.now(),
    );
    state = state.copyWith(messages: [...state.messages, sysMsg]);
    final text = _toolPrompt(toolId, toolLabel, payload);
    await sendMessage(text);
  }

  /// 工具需求 → 对话文本（prompt 键优先，其余键值平铺）
  static String _toolPrompt(
      String toolId, String toolLabel, Map<String, dynamic> payload) {
    final prompt = payload['prompt'] as String?;
    if (prompt != null && prompt.isNotEmpty) return '【$toolLabel】$prompt';
    final parts = [
      for (final e in payload.entries) '${e.key}: ${e.value}',
    ];
    return '【$toolLabel】${parts.join('，')}';
  }

  /// 上传类工具：先预签名上传附件，再作为对话附件触发
  Future<void> invokeToolWithFile(
      String toolId, String toolLabel, String filePath) async {
    try {
      final url = await _toolApi.uploadFile(filePath);
      await sendMessage('【$toolLabel】请处理附件', attachments: [url]);
    } on DioException catch (e) {
      state = state.copyWith(error: ApiException.fromDio(e).message);
    }
  }

  /// 错题变式练习入口：新开/复用会话，POST /mistakes/{id}/variations（SSE）
  /// 逐段流入 assistant 消息
  Future<void> startVariantPractice(String mistakeId, String question) async {
    newConversation();
    final convId = await _ensureConversation('变式练习');
    final sysMsg = ChatMessage(
      id: _uuid.v4(),
      conversationId: convId,
      role: MessageRole.system,
      contentType: MessageContentType.text,
      content: '正在使用 变式练习…',
      createdAt: DateTime.now(),
    );
    final assistantMsg = ChatMessage(
      id: _uuid.v4(),
      conversationId: convId,
      role: MessageRole.assistant,
      contentType: MessageContentType.text,
      content: '',
      createdAt: DateTime.now(),
      status: MessageStatus.streaming,
    );
    state = state.copyWith(messages: [sysMsg, assistantMsg]);
    try {
      await for (final delta in _userApi.mistakeVariations(mistakeId)) {
        _appendChunk(assistantMsg.id, delta);
      }
      _finishMessage('', assistantMsg.id);
    } on DioException catch (e) {
      _failMessage('', assistantMsg.id, ApiException.fromDio(e).message);
    } on ApiException catch (e) {
      _failMessage('', assistantMsg.id, e.message);
    }
  }

  /// 文档生成类工具（PPT/教案/试卷）：插入系统消息 → 创建异步任务 →
  /// 轮询状态 → 完成后追加对应预览卡片消息
  Future<void> runGenerationTask(
      String toolId, String toolLabel, Map<String, dynamic> payload) async {
    final convId = await _ensureConversation('[$toolLabel]');
    final sysMsg = ChatMessage(
      id: _uuid.v4(),
      conversationId: convId,
      role: MessageRole.system,
      contentType: MessageContentType.text,
      content: '正在使用 $toolLabel…',
      createdAt: DateTime.now(),
    );
    state = state.copyWith(messages: [...state.messages, sysMsg]);

    final String pollPath;
    try {
      pollPath = await _toolApi.createGenerationTask(toolId, payload);
    } on DioException catch (e) {
      state = state.copyWith(error: ApiException.fromDio(e).message);
      return;
    }
    if (pollPath.isEmpty) {
      state = state.copyWith(error: '创建生成任务失败');
      return;
    }

    // 轮询任务状态：每 2 秒一次，最多 60 秒
    const maxAttempts = 30;
    for (var attempt = 0; attempt < maxAttempts; attempt++) {
      await Future<void>.delayed(const Duration(seconds: 2));
      final Map<String, dynamic> task;
      try {
        task = await _toolApi.getGenerationTask(pollPath);
      } on DioException {
        continue; // 单次轮询失败，下个周期重试
      }
      final status = task['status'] as String? ?? 'generating';
      if (ToolApi.successStatuses.contains(status)) {
        final result = ToolApi.previewOf(toolId, task);
        final msg = ChatMessage(
          id: _uuid.v4(),
          conversationId: convId,
          role: MessageRole.assistant,
          contentType: _contentTypeFor(toolId),
          content: result['title'] as String? ?? '',
          toolResult: result,
          createdAt: DateTime.now(),
        );
        state = state.copyWith(messages: [...state.messages, msg]);
        return;
      }
      if (status == 'failed') {
        state = state.copyWith(
            error: (task['error'] ?? task['message']) as String? ??
                '$toolLabel 生成失败');
        return;
      }
    }
    state = state.copyWith(error: '$toolLabel 生成超时，可稍后在教师工作台资源库查看');
  }

  /// 发送语音消息：先上传再作为附件发送
  Future<void> sendAudio(String filePath) async {
    try {
      final url = await _toolApi.uploadFile(filePath);
      await sendMessage('[语音]', attachments: [url]);
    } on DioException catch (e) {
      state = state.copyWith(error: ApiException.fromDio(e).message);
    }
  }

  void toggleWebSearch() =>
      state = state.copyWith(webSearchEnabled: !state.webSearchEnabled);

  void clearError() => state = state.copyWith();

  MessageContentType _contentTypeFor(String toolId) => switch (toolId) {
        'make_ppt' => MessageContentType.pptPreview,
        'make_lesson_plan' => MessageContentType.lessonPlanPreview,
        'make_exam' => MessageContentType.examPreview,
        'grade_homework' => MessageContentType.toolResult,
        _ => MessageContentType.toolResult,
      };
}

final chatProvider =
    StateNotifierProvider<ChatNotifier, ChatState>((ref) => ChatNotifier(ref));

/// 外部向聊天输入框插入文本的信号（公式编辑器等），ChatInput 监听消费后清空
final chatInputInsertProvider = StateProvider<String?>((ref) => null);
