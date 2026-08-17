import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:hive_flutter/hive_flutter.dart';
import 'package:sqflite/sqflite.dart';

import '../../models/conversation.dart';
import '../../models/message.dart';

/// 本地存储：
/// - Hive：设置(settings) / 输入草稿(drafts) / 离线发送队列(offline_queue)
/// - sqflite：会话与消息结构化缓存
class LocalDb {
  LocalDb._({
    required this.settings,
    required this.drafts,
    required this.offlineQueue,
    required this.db,
  });

  final Box<dynamic> settings;
  final Box<String> drafts;
  final Box<String> offlineQueue;
  final Database db;

  static Future<LocalDb> init() async {
    await Hive.initFlutter();
    final settings = await Hive.openBox<dynamic>('settings');
    final drafts = await Hive.openBox<String>('drafts');
    final offlineQueue = await Hive.openBox<String>('offline_queue');

    final dir = await getDatabasesPath();
    final db = await openDatabase(
      '$dir/edu_cache.db',
      version: 1,
      onCreate: (db, version) async {
        await db.execute('''
          CREATE TABLE conversations(
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            message_count INTEGER NOT NULL DEFAULT 0
          )
        ''');
        await db.execute('''
          CREATE TABLE messages(
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content_type TEXT NOT NULL,
            content TEXT NOT NULL,
            tool_result TEXT,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL
          )
        ''');
        await db.execute(
            'CREATE INDEX idx_messages_conv ON messages(conversation_id, created_at)');
      },
    );
    return LocalDb._(
        settings: settings, drafts: drafts, offlineQueue: offlineQueue, db: db);
  }

  // ---------------- 消息缓存 ----------------

  Future<void> cacheMessages(String conversationId, List<ChatMessage> msgs) async {
    final batch = db.batch();
    for (final m in msgs) {
      batch.insert(
        'messages',
        {
          'id': m.id,
          'conversation_id': m.conversationId,
          'role': m.role.name,
          'content_type': m.contentType.name,
          'content': m.content,
          'tool_result': m.toolResult == null ? null : jsonEncode(m.toolResult),
          'created_at': m.createdAt.toIso8601String(),
          'status': m.status.name,
        },
        conflictAlgorithm: ConflictAlgorithm.replace,
      );
    }
    await batch.commit(noResult: true);
  }

  Future<List<ChatMessage>> loadMessages(String conversationId) async {
    final rows = await db.query(
      'messages',
      where: 'conversation_id = ?',
      whereArgs: [conversationId],
      orderBy: 'created_at ASC',
    );
    return rows
        .map((r) => ChatMessage.fromJson({
              'id': r['id'],
              'conversationId': r['conversation_id'],
              'role': r['role'],
              'contentType': r['content_type'],
              'content': r['content'],
              'toolResult': r['tool_result'] == null
                  ? null
                  : jsonDecode(r['tool_result'] as String),
              'createdAt': r['created_at'],
              'status': r['status'],
            }))
        .toList();
  }

  Future<void> cacheConversations(List<Conversation> list) async {
    final batch = db.batch();
    for (final c in list) {
      batch.insert(
        'conversations',
        {
          'id': c.id,
          'title': c.title,
          'updated_at': c.updatedAt.toIso8601String(),
          'message_count': c.messageCount,
        },
        conflictAlgorithm: ConflictAlgorithm.replace,
      );
    }
    await batch.commit(noResult: true);
  }

  Future<List<Conversation>> loadConversations() async {
    final rows = await db.query('conversations', orderBy: 'updated_at DESC');
    return rows
        .map((r) => Conversation.fromJson({
              'id': r['id'],
              'title': r['title'],
              'updatedAt': r['updated_at'],
              'messageCount': r['message_count'],
            }))
        .toList();
  }

  // ---------------- 离线队列 ----------------

  /// 消息发送失败时入队（JSON 字符串），网络恢复后重发
  Future<void> enqueueOffline(Map<String, dynamic> payload) =>
      offlineQueue.add(jsonEncode(payload));

  List<Map<String, dynamic>> pendingOffline() => offlineQueue.values
      .map((s) => jsonDecode(s) as Map<String, dynamic>)
      .toList();

  Future<void> clearOffline() => offlineQueue.clear();

  // ---------------- 草稿 ----------------

  String? draftOf(String conversationId) => drafts.get(conversationId);

  Future<void> saveDraft(String conversationId, String text) =>
      drafts.put(conversationId, text);
}

/// 在 main() 中初始化后 override
final localDbProvider = Provider<LocalDb>(
  (ref) => throw UnimplementedError('localDbProvider 需在 main() 中 override'),
);
