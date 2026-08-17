import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../models/user.dart';
import 'auth_provider.dart';

/// 注册页
class RegisterPage extends ConsumerStatefulWidget {
  const RegisterPage({super.key});

  @override
  ConsumerState<RegisterPage> createState() => _RegisterPageState();
}

class _RegisterPageState extends ConsumerState<RegisterPage> {
  final _formKey = GlobalKey<FormState>();
  final _usernameCtrl = TextEditingController();
  final _passwordCtrl = TextEditingController();
  final _nicknameCtrl = TextEditingController();
  UserMode _mode = UserMode.student;
  bool _loading = false;
  String? _error;

  @override
  void dispose() {
    _usernameCtrl.dispose();
    _passwordCtrl.dispose();
    _nicknameCtrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!(_formKey.currentState?.validate() ?? false)) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    final error = await ref.read(authProvider.notifier).register(
          username: _usernameCtrl.text.trim(),
          password: _passwordCtrl.text,
          nickname: _nicknameCtrl.text.trim(),
          mode: _mode,
        );
    if (!mounted) return;
    if (error == null) {
      context.go('/chat');
      return;
    }
    setState(() {
      _loading = false;
      _error = error;
    });
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(title: const Text('注册')),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 16),
          child: Form(
            key: _formKey,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text('创建账号',
                    style: Theme.of(context).textTheme.headlineSmall),
                const SizedBox(height: 6),
                Text('完善信息后即可开始智能学习之旅',
                    style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        color: Theme.of(context).colorScheme.onSurfaceVariant)),
                const SizedBox(height: 24),
                TextFormField(
                  controller: _usernameCtrl,
                  keyboardType: TextInputType.phone,
                  decoration: const InputDecoration(
                      labelText: '手机号', prefixIcon: Icon(Icons.phone_iphone)),
                  validator: (v) =>
                      (v == null || !RegExp(r'^1[3-9]\d{9}$').hasMatch(v.trim()))
                          ? '请输入正确的手机号'
                          : null,
                ),
                const SizedBox(height: 16),
                TextFormField(
                  controller: _nicknameCtrl,
                  decoration: const InputDecoration(
                      labelText: '昵称', prefixIcon: Icon(Icons.badge_outlined)),
                  validator: (v) =>
                      (v == null || v.trim().isEmpty) ? '请输入昵称' : null,
                ),
                const SizedBox(height: 16),
                TextFormField(
                  controller: _passwordCtrl,
                  obscureText: true,
                  decoration: const InputDecoration(
                      labelText: '密码', prefixIcon: Icon(Icons.lock_outline)),
                  validator: (v) =>
                      (v == null || v.length < 8) ? '密码至少 8 位' : null,
                ),
                const SizedBox(height: 28),
                Text('选择身份', style: Theme.of(context).textTheme.titleSmall),
                const SizedBox(height: 10),
                SegmentedButton<UserMode>(
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
                  selected: {_mode},
                  onSelectionChanged: (s) => setState(() => _mode = s.first),
                ),
                if (_error != null) ...[
                  const SizedBox(height: 16),
                  Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 12, vertical: 10),
                    decoration: BoxDecoration(
                      color: scheme.errorContainer.withValues(alpha: 0.5),
                      borderRadius: BorderRadius.circular(AppTokens.radiusMd),
                    ),
                    child: Row(
                      children: [
                        Icon(Icons.error_outline,
                            size: 18, color: scheme.onErrorContainer),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(_error!,
                              style: TextStyle(
                                  fontSize: 13, color: scheme.onErrorContainer)),
                        ),
                      ],
                    ),
                  ),
                ],
                const SizedBox(height: 28),
                FilledButton(
                  onPressed: _loading ? null : _submit,
                  child: _loading
                      ? const SizedBox(
                          height: 20,
                          width: 20,
                          child: CircularProgressIndicator(strokeWidth: 2))
                      : const Text('注册并登录'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
