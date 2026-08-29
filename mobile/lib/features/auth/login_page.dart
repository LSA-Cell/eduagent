import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/network/api/auth_api.dart';
import '../../core/network/dio_client.dart';
import 'auth_provider.dart';

/// 登录页：密码登录 / 短信验证码登录 / 游客体验（不强制登录）
class LoginPage extends ConsumerStatefulWidget {
  const LoginPage({super.key});

  @override
  ConsumerState<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends ConsumerState<LoginPage> {
  final _formKey = GlobalKey<FormState>();
  final _usernameCtrl = TextEditingController();
  final _passwordCtrl = TextEditingController();
  final _smsCodeCtrl = TextEditingController();
  final _captchaCtrl = TextEditingController();
  bool _smsMode = false; // 验证码登录模式
  bool _loading = false;
  bool _obscure = true;
  String? _error;
  int _smsCountdown = 0;

  // 连续失败后服务端要求图形验证码
  CaptchaChallenge? _captcha;

  @override
  void dispose() {
    _usernameCtrl.dispose();
    _passwordCtrl.dispose();
    _smsCodeCtrl.dispose();
    _captchaCtrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!(_formKey.currentState?.validate() ?? false)) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      if (_smsMode) {
        final error = await ref.read(authProvider.notifier).loginSms(
            _usernameCtrl.text.trim(), _smsCodeCtrl.text.trim());
        if (error != null) throw ApiException(error);
      } else {
        await ref.read(authProvider.notifier).login(
              _usernameCtrl.text.trim(),
              _passwordCtrl.text,
              captchaId: _captcha?.id,
              captchaCode:
                  _captcha == null ? null : _captchaCtrl.text.trim(),
            );
      }
      // 登录成功由路由 redirect 自动跳转 /chat
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.message;
        _loading = false;
      });
      // 服务端要求图形验证码：拉取并展示
      if (e.code == 'captcha_required') await _refreshCaptcha();
      return;
    }
    if (!mounted) return;
    setState(() => _loading = false);
  }

  Future<void> _refreshCaptcha() async {
    try {
      final captcha = await ref.read(authApiProvider).fetchCaptcha();
      if (mounted) {
        setState(() {
          _captcha = captcha;
          _captchaCtrl.clear();
        });
      }
    } catch (_) {}
  }

  Future<void> _sendSmsCode(String scene) async {
    final phone = _usernameCtrl.text.trim();
    if (!RegExp(r'^1[3-9]\d{9}$').hasMatch(phone)) {
      setState(() => _error = '请先输入正确的手机号');
      return;
    }
    try {
      await ref.read(authApiProvider).sendSmsCode(phone, scene);
      if (!mounted) return;
      setState(() {
        _error = null;
        _smsCountdown = 60;
      });
      unawaited(_tickCountdown());
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('验证码已发送')));
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } catch (_) {
      setState(() => _error = '发送失败，请稍后再试');
    }
  }

  Future<void> _tickCountdown() async {
    while (_smsCountdown > 0 && mounted) {
      await Future<void>.delayed(const Duration(seconds: 1));
      if (mounted) setState(() => _smsCountdown--);
    }
  }

  Future<void> _enterGuest() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    final error = await ref.read(authProvider.notifier).enterGuest();
    if (!mounted) return;
    setState(() {
      _loading = false;
      _error = error;
    });
    // 成功由路由 redirect 放行到主界面
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 24),
            child: Form(
              key: _formKey,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  // 品牌标识：描边方块（签名元素留给语音录制按钮）
                  Center(
                    child: Container(
                      width: 84,
                      height: 84,
                      decoration: BoxDecoration(
                        borderRadius:
                            BorderRadius.circular(AppTokens.radiusXl + 6),
                        border: Border.all(color: scheme.primary, width: 1.5),
                      ),
                      child: Icon(Icons.school,
                          size: 44, color: scheme.primary),
                    ),
                  ),
                  const SizedBox(height: 20),
                  Text(
                    'EduAgent',
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.headlineSmall,
                  ),
                  const SizedBox(height: 6),
                  Text(
                    '你的智能学习助手',
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        color: Theme.of(context).colorScheme.onSurfaceVariant),
                  ),
                  const SizedBox(height: 28),

                  // 登录方式切换
                  SegmentedButton<bool>(
                    segments: const [
                      ButtonSegment(
                          value: false,
                          label: Text('密码登录'),
                          icon: Icon(Icons.lock_outline)),
                      ButtonSegment(
                          value: true,
                          label: Text('验证码登录'),
                          icon: Icon(Icons.sms_outlined)),
                    ],
                    selected: {_smsMode},
                    onSelectionChanged: (s) => setState(() {
                      _smsMode = s.first;
                      _error = null;
                    }),
                  ),
                  const SizedBox(height: 20),

                  TextFormField(
                    controller: _usernameCtrl,
                    keyboardType: TextInputType.phone,
                    decoration: const InputDecoration(
                        labelText: '手机号', prefixIcon: Icon(Icons.person)),
                    validator: (v) => (v == null ||
                            !RegExp(r'^1[3-9]\d{9}$').hasMatch(v.trim()))
                        ? '请输入正确的手机号'
                        : null,
                  ),
                  const SizedBox(height: 16),

                  if (!_smsMode)
                    TextFormField(
                      controller: _passwordCtrl,
                      obscureText: _obscure,
                      decoration: InputDecoration(
                        labelText: '密码',
                        prefixIcon: const Icon(Icons.lock),
                        suffixIcon: IconButton(
                          icon: Icon(
                              _obscure ? Icons.visibility_off : Icons.visibility),
                          onPressed: () => setState(() => _obscure = !_obscure),
                        ),
                      ),
                      // 登录不限制密码长度（设置密码统一 8 位，见注册/改密）
                      validator: (v) =>
                          (v == null || v.isEmpty) ? '请输入密码' : null,
                    )
                  else
                    TextFormField(
                      controller: _smsCodeCtrl,
                      keyboardType: TextInputType.number,
                      decoration: InputDecoration(
                        labelText: '短信验证码',
                        prefixIcon: const Icon(Icons.pin_outlined),
                        suffixIcon: TextButton(
                          onPressed:
                              _smsCountdown > 0 ? null : () => _sendSmsCode('login'),
                          child: Text(_smsCountdown > 0
                              ? '${_smsCountdown}s 后重发'
                              : '获取验证码'),
                        ),
                      ),
                      validator: (v) =>
                          (v == null || v.trim().length < 4) ? '请输入验证码' : null,
                    ),

                  // 图形验证码（连续失败后被服务端要求时出现）
                  if (_captcha != null) ...[
                    const SizedBox(height: 16),
                    Row(
                      children: [
                        Expanded(
                          child: TextFormField(
                            controller: _captchaCtrl,
                            decoration: const InputDecoration(
                                labelText: '图形验证码',
                                prefixIcon: Icon(Icons.security)),
                            validator: (v) => (v == null || v.trim().isEmpty)
                                ? '请输入图形验证码'
                                : null,
                          ),
                        ),
                        const SizedBox(width: 10),
                        GestureDetector(
                          onTap: _refreshCaptcha,
                          child: ClipRRect(
                            borderRadius:
                                BorderRadius.circular(AppTokens.radiusMd),
                            child: SvgPicture.string(
                              _captcha!.svg,
                              width: 132,
                              height: 44,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ],

                  if (_error != null) ...[
                    const SizedBox(height: 16),
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 12, vertical: 10),
                      decoration: BoxDecoration(
                        color: scheme.errorContainer.withValues(alpha: 0.5),
                        borderRadius:
                            BorderRadius.circular(AppTokens.radiusMd),
                      ),
                      child: Row(
                        children: [
                          Icon(Icons.error_outline,
                              size: 18, color: scheme.onErrorContainer),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(_error!,
                                style: TextStyle(
                                    fontSize: 13,
                                    color: scheme.onErrorContainer)),
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
                        : const Text('登录'),
                  ),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      TextButton(
                        onPressed: () => _showResetPasswordSheet(context),
                        child: const Text('忘记密码'),
                      ),
                      TextButton(
                        onPressed: () => context.push('/register'),
                        child: const Text('没有账号？去注册'),
                      ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  // 游客入口：不强制登录
                  OutlinedButton.icon(
                    onPressed: _loading ? null : _enterGuest,
                    icon: const Icon(Icons.explore_outlined, size: 18),
                    label: const Text('暂不登录，游客体验'),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  /// 找回密码：短信验证码核验后重置
  void _showResetPasswordSheet(BuildContext context) {
    final phoneCtrl = TextEditingController(text: _usernameCtrl.text.trim());
    final codeCtrl = TextEditingController();
    final newPwdCtrl = TextEditingController();
    String? error;
    bool sending = false;

    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (sheetContext) => StatefulBuilder(
        builder: (sheetContext, setSheetState) => Padding(
          padding: EdgeInsets.fromLTRB(
              24, 20, 24, MediaQuery.of(sheetContext).viewInsets.bottom + 24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text('找回密码', style: Theme.of(sheetContext).textTheme.titleLarge),
              const SizedBox(height: 16),
              TextField(
                controller: phoneCtrl,
                keyboardType: TextInputType.phone,
                decoration: const InputDecoration(labelText: '手机号'),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: codeCtrl,
                keyboardType: TextInputType.number,
                decoration: InputDecoration(
                  labelText: '短信验证码',
                  suffixIcon: TextButton(
                    onPressed: sending
                        ? null
                        : () async {
                            setSheetState(() => sending = true);
                            try {
                              await ref
                                  .read(authApiProvider)
                                  .sendSmsCode(phoneCtrl.text.trim(), 'reset');
                              setSheetState(() => error = null);
                            } on ApiException catch (e) {
                              setSheetState(() => error = e.message);
                            } catch (_) {
                              setSheetState(() => error = '发送失败，请稍后再试');
                            } finally {
                              setSheetState(() => sending = false);
                            }
                          },
                    child: const Text('获取验证码'),
                  ),
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: newPwdCtrl,
                obscureText: true,
                decoration: const InputDecoration(
                    labelText: '新密码', helperText: '至少 8 位'),
              ),
              if (error != null) ...[
                const SizedBox(height: 10),
                Text(error!,
                    style: TextStyle(
                        fontSize: 13,
                        color: Theme.of(sheetContext).colorScheme.error)),
              ],
              const SizedBox(height: 20),
              FilledButton(
                onPressed: () async {
                  try {
                    await ref.read(authApiProvider).resetPassword(
                        phoneCtrl.text.trim(),
                        codeCtrl.text.trim(),
                        newPwdCtrl.text);
                    if (sheetContext.mounted) {
                      Navigator.of(sheetContext).pop();
                      ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(content: Text('密码已重置，请用新密码登录')));
                    }
                  } on ApiException catch (e) {
                    setSheetState(() => error = e.message);
                  } catch (_) {
                    setSheetState(() => error = '重置失败，请稍后再试');
                  }
                },
                child: const Text('重置密码'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
