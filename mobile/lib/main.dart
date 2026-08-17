import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app/router.dart';
import 'app/theme.dart';
import 'core/storage/local_db.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // 初始化本地存储（Hive boxes + sqflite）
  final localDb = await LocalDb.init();
  runApp(
    ProviderScope(
      overrides: [localDbProvider.overrideWithValue(localDb)],
      child: const EduAgentApp(),
    ),
  );
}

class EduAgentApp extends ConsumerWidget {
  const EduAgentApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final router = ref.watch(routerProvider);
    final theme = ref.watch(appThemeProvider);
    return MaterialApp.router(
      title: 'EduAgent',
      debugShowCheckedModeBanner: false,
      theme: theme.light,
      darkTheme: theme.dark,
      routerConfig: router,
    );
  }
}
