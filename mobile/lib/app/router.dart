import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../features/auth/auth_provider.dart';
import '../features/auth/login_page.dart';
import '../features/auth/register_page.dart';
import '../features/chat/chat_page.dart';
import '../features/flashcards/flashcards_page.dart';
import '../features/mistakes/mistakes_page.dart';
import '../features/plans/plans_page.dart';
import '../features/profile/profile_page.dart';
import '../features/profile/profile_provider.dart';
import '../features/teacher/analytics_report_page.dart';
import '../features/teacher/teacher_page.dart';
import '../features/voice/voice_page.dart';
import '../models/analytics.dart';
import '../models/user.dart';

/// 路由表：
/// - /login、/register：未登录
/// - 主 Shell（底部导航）：/chat、/mistakes、/flashcards、/teacher（教师模式显示）、/profile
/// - 全屏独立页：/voice、/plans、/teacher/report（学情分析报告）
final routerProvider = Provider<GoRouter>((ref) {
  // 登录态变化时触发 redirect 重算
  final refresh = ValueNotifier(0);
  ref.listen(authProvider, (_, __) => refresh.value++);
  ref.onDispose(refresh.dispose);

  return GoRouter(
    initialLocation: '/chat',
    refreshListenable: refresh,
    redirect: (context, state) {
      final auth = ref.read(authProvider);
      final loggingIn = state.matchedLocation == '/login' ||
          state.matchedLocation == '/register';
      switch (auth.status) {
        case AuthStatus.unknown:
          return null; // 启动中，保持
        case AuthStatus.unauthenticated:
          return loggingIn ? null : '/login';
        case AuthStatus.authenticated:
          if (loggingIn) return '/chat';
          // 教师 Tab 仅教师可直达
          if (state.matchedLocation == '/teacher' &&
              ref.read(userModeProvider) != UserMode.teacher) {
            return '/chat';
          }
          return null;
      }
    },
    routes: [
      GoRoute(path: '/login', builder: (_, __) => const LoginPage()),
      GoRoute(path: '/register', builder: (_, __) => const RegisterPage()),

      // 全屏独立页
      GoRoute(path: '/voice', builder: (_, __) => const VoicePage()),
      GoRoute(path: '/plans', builder: (_, __) => const PlansPage()),
      // 学情分析报告（由学情分析工具 Sheet 生成后携带报告数据进入）
      GoRoute(
        path: '/teacher/report',
        builder: (context, state) {
          final report = state.extra;
          if (report is! AnalyticsReport) {
            // 直接访问/数据丢失时的兜底页
            return Scaffold(
              appBar: AppBar(title: const Text('学情分析报告')),
              body: const Center(child: Text('报告数据缺失，请从学情分析工具生成')),
            );
          }
          return AnalyticsReportPage(report: report);
        },
      ),

      // 主 Shell：底部导航
      StatefulShellRoute.indexedStack(
        builder: (context, state, navigationShell) =>
            HomeShell(shell: navigationShell),
        branches: [
          StatefulShellBranch(routes: [
            GoRoute(path: '/chat', builder: (_, __) => const ChatPage()),
          ]),
          StatefulShellBranch(routes: [
            GoRoute(
                path: '/mistakes', builder: (_, __) => const MistakesPage()),
          ]),
          StatefulShellBranch(routes: [
            GoRoute(
                path: '/flashcards',
                builder: (_, __) => const FlashcardsPage()),
          ]),
          StatefulShellBranch(routes: [
            GoRoute(
                path: '/teacher', builder: (_, __) => const TeacherPage()),
          ]),
          StatefulShellBranch(routes: [
            GoRoute(
                path: '/profile', builder: (_, __) => const ProfilePage()),
          ]),
        ],
      ),
    ],
  );
});

/// 底部导航 Shell：教师 Tab 按身份显隐
class HomeShell extends ConsumerWidget {
  const HomeShell({super.key, required this.shell});

  final StatefulNavigationShell shell;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final isTeacher = ref.watch(userModeProvider) == UserMode.teacher;

    // 可见 Tab：教师模式显示教师工作台
    final tabs = [
      (icon: Icons.chat_bubble_outline, label: '聊天', branch: 0),
      (icon: Icons.bookmark_outline, label: '错题本', branch: 1),
      (icon: Icons.style_outlined, label: '卡片', branch: 2),
      if (isTeacher)
        (icon: Icons.co_present_outlined, label: '教师', branch: 3),
      (icon: Icons.person_outline, label: '我的', branch: 4),
    ];

    // 当前 branch 映射到可见列表下标（学生隐藏教师 Tab 后仍保持选中正确）
    final currentBranch = shell.currentIndex;
    var selectedIndex = tabs.indexWhere((t) => t.branch == currentBranch);
    if (selectedIndex < 0) selectedIndex = 0;

    return Scaffold(
      body: shell,
      bottomNavigationBar: NavigationBar(
        selectedIndex: selectedIndex,
        onDestinationSelected: (i) => shell.goBranch(
          tabs[i].branch,
          initialLocation: tabs[i].branch == shell.currentIndex,
        ),
        destinations: [
          for (final t in tabs)
            NavigationDestination(icon: Icon(t.icon), label: t.label),
        ],
      ),
    );
  }
}
