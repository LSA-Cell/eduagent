import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/network/api/teacher_api.dart';
import '../../core/network/api/tool_api.dart';
import '../../core/network/api/user_api.dart';
import '../../core/storage/local_db.dart';
import '../../core/storage/secure_storage.dart';
import '../../models/user.dart';
import '../auth/auth_provider.dart';
import 'profile_provider.dart';

/// 已保存 API Key（掩码列表）
final apiKeysProvider =
    FutureProvider.autoDispose<List<ApiKeyEntry>>((ref) async {
  return ref.read(userApiProvider).listApiKeys();
});

/// "我的"页：身份切换 / 模型选择 / 学科年级设置 / API Key 管理 / 教师认证
class ProfilePage extends ConsumerWidget {
  const ProfilePage({super.key});

  static const _grades = ['初一', '初二', '初三', '高一', '高二', '高三', '大学'];
  static const _subjects = ['数学', '语文', '英语', '物理', '化学', '生物', '历史', '地理'];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authProvider);
    final mode = ref.watch(userModeProvider);
    final user = auth.user;
    final scheme = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(title: const Text('我的')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        children: [
          // 用户头部卡片
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Row(
                children: [
                  Container(
                    width: 56,
                    height: 56,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      border: Border.all(color: scheme.primary, width: 1.5),
                    ),
                    clipBehavior: Clip.antiAlias,
                    child: user?.avatarUrl == null
                        ? Icon(Icons.person, size: 30, color: scheme.primary)
                        : Image.network(user!.avatarUrl!, fit: BoxFit.cover),
                  ),
                  const SizedBox(width: 14),
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(user?.nickname ?? '未登录',
                          style: Theme.of(context).textTheme.titleMedium),
                      const SizedBox(height: 4),
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 8, vertical: 2),
                        decoration: BoxDecoration(
                          color:
                              scheme.primaryContainer.withValues(alpha: 0.55),
                          borderRadius: BorderRadius.circular(999),
                        ),
                        child: Text(
                          _modeLabel(mode),
                          style: TextStyle(
                              fontSize: 11,
                              fontWeight: FontWeight.w600,
                              color: scheme.primary),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),

          // 身份切换
          _SectionCard(
            title: '身份',
            child: SegmentedButton<UserMode>(
              segments: const [
                ButtonSegment(
                    value: UserMode.student,
                    label: Text('学生'),
                    icon: Icon(Icons.school)),
                ButtonSegment(
                    value: UserMode.teacher,
                    label: Text('教师'),
                    icon: Icon(Icons.co_present)),
                ButtonSegment(
                    value: UserMode.parent,
                    label: Text('家长'),
                    icon: Icon(Icons.family_restroom)),
              ],
              selected: {mode},
              onSelectionChanged: (s) =>
                  ref.read(authProvider.notifier).setMode(s.first),
            ),
          ),
          const SizedBox(height: 12),

          // 模型选择
          _SectionCard(
            title: '模型选择',
            child: ref.watch(modelListProvider).when(
                  loading: () => const LinearProgressIndicator(),
                  error: (_, __) => const Text('模型列表加载失败'),
                  data: (models) => DropdownButtonFormField<String>(
                    value: models.contains(user?.selectedModel)
                        ? user?.selectedModel
                        : models.first,
                    decoration: const InputDecoration(labelText: '默认模型'),
                    items: [
                      for (final m in models)
                        DropdownMenuItem(
                            value: m,
                            child: Text(m == 'auto' ? '自动（双轨路由）' : m)),
                    ],
                    onChanged: (v) {
                      if (v != null) {
                        ref.read(authProvider.notifier).updateModel(v);
                      }
                    },
                  ),
                ),
          ),
          const SizedBox(height: 12),

          // 年级 / 学科设置
          _SectionCard(
            title: '年级',
            child: Wrap(
              spacing: 8,
              children: [
                for (final g in _grades)
                  ChoiceChip(
                    label: Text(g),
                    selected: user?.grade == g,
                    onSelected: (_) => _saveProfile(ref, {'grade': g}),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 12),
          _SectionCard(
            title: '学科',
            child: Wrap(
              spacing: 8,
              runSpacing: 4,
              children: [
                for (final s in _subjects)
                  FilterChip(
                    label: Text(s),
                    selected: user?.subjects.contains(s) ?? false,
                    onSelected: (on) {
                      final current = [...(user?.subjects ?? const <String>[])];
                      on ? current.add(s) : current.remove(s);
                      _saveProfile(ref, {'subjects': current});
                    },
                  ),
              ],
            ),
          ),
          const SizedBox(height: 12),

          // API Key 管理（只显示掩码）
          _SectionCard(
            title: 'API Key 管理',
            trailing: TextButton.icon(
              onPressed: () => _showAddKeyDialog(context, ref),
              icon: const Icon(Icons.add, size: 18),
              label: const Text('添加'),
            ),
            child: ref.watch(apiKeysProvider).when(
                  loading: () => const LinearProgressIndicator(),
                  error: (_, __) => const Text('加载失败'),
                  data: (keys) => keys.isEmpty
                      ? Text('未配置自有 API Key，将使用平台模型',
                          style: Theme.of(context).textTheme.bodySmall)
                      : Column(
                          children: [
                            for (final k in keys)
                              ListTile(
                                dense: true,
                                contentPadding: EdgeInsets.zero,
                                leading: Container(
                                  width: 34,
                                  height: 34,
                                  decoration: BoxDecoration(
                                    color: scheme.surfaceContainerHighest
                                        .withValues(alpha: 0.7),
                                    borderRadius: BorderRadius.circular(10),
                                  ),
                                  child: Icon(Icons.key,
                                      size: 18,
                                      color: scheme.onSurfaceVariant),
                                ),
                                title: Text(k.provider),
                                subtitle: Text(k.maskedKey), // 只显示掩码
                                trailing: IconButton(
                                  icon: Icon(Icons.delete_outline,
                                      color: scheme.error),
                                  onPressed: () async {
                                    await ref
                                        .read(userApiProvider)
                                        .deleteApiKey(k.id);
                                    await ref
                                        .read(secureStorageProvider)
                                        .deleteApiKey(k.provider);
                                    ref.invalidate(apiKeysProvider);
                                  },
                                ),
                              ),
                          ],
                        ),
                ),
          ),

          // 教师认证
          if (mode == UserMode.teacher) ...[
            const SizedBox(height: 12),
            _SectionCard(
              title: '教师认证',
              child: ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Container(
                  width: 38,
                  height: 38,
                  decoration: BoxDecoration(
                    color: user?.teacherVerified == true
                        ? Colors.green.withValues(alpha: 0.12)
                        : scheme.surfaceContainerHighest
                            .withValues(alpha: 0.7),
                    borderRadius: BorderRadius.circular(AppTokens.radiusMd),
                  ),
                  child: Icon(
                    user?.teacherVerified == true
                        ? Icons.verified
                        : Icons.upload_file,
                    size: 20,
                    color: user?.teacherVerified == true
                        ? Colors.green
                        : scheme.onSurfaceVariant,
                  ),
                ),
                title: Text(user?.teacherVerified == true
                    ? '已认证'
                    : '上传教师资格证/在职证明'),
                subtitle: user?.teacherVerified == true
                    ? null
                    : const Text('PDF 或图片'),
                onTap: user?.teacherVerified == true
                    ? null
                    : () => _uploadCert(context, ref),
              ),
            ),
          ],
          const SizedBox(height: 20),

          // 退出登录
          OutlinedButton.icon(
            onPressed: () async {
              await ref.read(authProvider.notifier).logout();
              if (context.mounted) context.go('/login');
            },
            style: OutlinedButton.styleFrom(
              foregroundColor: scheme.error,
              side: BorderSide(color: scheme.error.withValues(alpha: 0.4)),
            ),
            icon: const Icon(Icons.logout),
            label: const Text('退出登录'),
          ),
        ],
      ),
    );
  }

  String _modeLabel(UserMode mode) => switch (mode) {
        UserMode.student => '学生',
        UserMode.teacher => '教师',
        UserMode.parent => '家长',
      };

  Future<void> _saveProfile(WidgetRef ref, Map<String, dynamic> patch) async {
    try {
      await ref.read(userApiProvider).updateProfile(patch);
      // 刷新本地设置缓存
      await ref.read(localDbProvider).settings.put('profile_patch', patch);
    } catch (_) {}
  }

  Future<void> _uploadCert(BuildContext context, WidgetRef ref) async {
    final files = await FilePicker.pickFiles(
        type: FileType.custom, allowedExtensions: ['pdf', 'jpg', 'png']);
    final path = files.isEmpty ? null : files.single.path;
    if (path == null) return;
    try {
      // 先经预签名地址上传文件，再提交认证（202 受理，待审核）
      final url = await ref.read(toolApiProvider).uploadFile(path);
      await ref.read(teacherApiProvider).uploadCertification(url);
      if (context.mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('已提交，等待审核')));
      }
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('上传失败，请重试')));
      }
    }
  }

  Future<void> _showAddKeyDialog(BuildContext context, WidgetRef ref) async {
    final providerCtrl = TextEditingController(text: 'openai');
    final keyCtrl = TextEditingController();
    final saved = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('添加 API Key'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: providerCtrl,
              decoration: const InputDecoration(
                  labelText: '厂商（openai/deepseek/claude/gemini）'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: keyCtrl,
              obscureText: true,
              decoration: const InputDecoration(
                  labelText: 'API Key', helperText: '仅加密存储，此后只显示掩码'),
            ),
          ],
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: const Text('取消')),
          FilledButton(
              onPressed: () => Navigator.of(dialogContext).pop(true),
              child: const Text('保存')),
        ],
      ),
    );
    if (saved != true) return;
    final provider = providerCtrl.text.trim();
    final key = keyCtrl.text.trim();
    if (provider.isEmpty || key.isEmpty) return;
    try {
      // 明文只经 HTTPS 提交后端加密存储 + 本地安全存储，不落 Hive/SQLite
      await ref.read(userApiProvider).addApiKey(provider, key);
      await ref.read(secureStorageProvider).saveApiKey(provider, key);
      ref.invalidate(apiKeysProvider);
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('保存失败，请重试')));
      }
    }
  }
}

/// 设置分区卡：标题（+ 可选操作）+ 内容
class _SectionCard extends StatelessWidget {
  const _SectionCard({
    required this.title,
    required this.child,
    this.trailing,
  });

  final String title;
  final Widget child;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text(title, style: Theme.of(context).textTheme.titleSmall),
                const Spacer(),
                if (trailing != null) trailing!,
              ],
            ),
            const SizedBox(height: 10),
            child,
          ],
        ),
      ),
    );
  }
}
