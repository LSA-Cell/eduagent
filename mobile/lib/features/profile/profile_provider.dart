import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api/user_api.dart';
import '../../models/user.dart';

/// 当前用户身份：驱动主题色、工具栏重排、底部导航教师 Tab 显隐。
/// 登录成功后由 authProvider 同步；"我的"页可手动切换。
final userModeProvider = StateProvider<UserMode>((ref) => UserMode.student);

/// 可选模型列表（"我的"页登录后加载）
final modelListProvider = FutureProvider<List<String>>((ref) async {
  try {
    return await ref.read(userApiProvider).listModels();
  } catch (_) {
    return const ['auto']; // 未登录或接口不可用时回退
  }
});
