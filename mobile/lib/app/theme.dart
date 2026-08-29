import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../features/profile/profile_provider.dart';
import '../models/user.dart';

/// 应用主题（Material3）。学生/家长与教师使用不同主题色。
class AppTheme {
  const AppTheme({required this.light, required this.dark});

  final ThemeData light;
  final ThemeData dark;
}

/// Claude 式古典配色：暖米白纸面 + 墨褐文字 + 赤陶单一强调色。
class AppPalette {
  AppPalette._();

  // 浅色模式
  static const Color paper = Color(0xFFF6F2EB); // 羊皮纸底
  static const Color cardLight = Color(0xFFFDFBF5); // 纸面卡片
  static const Color ink = Color(0xFF26201A); // 墨褐正文
  static const Color inkSoft = Color(0xFF6E6459); // 次要文字
  static const Color hairlineLight = Color(0xFFE2DACD); // 暖灰描边

  // 深色模式（暖调炭黑）
  static const Color soot = Color(0xFF1D1915);
  static const Color cardDark = Color(0xFF282219);
  static const Color paperInk = Color(0xFFEDE6D8); // 深色模式正文
  static const Color hairlineDark = Color(0xFF3D362B);

  // 强调色：赤陶（terracotta）
  static const Color terracotta = Color(0xFFC15F3C);

  // 语义状态色（古典低饱和）：掌握度 / 评分 / 认证徽标
  static const Color sage = Color(0xFF5F7A5A); // 掌握 / 通过 / 已完成
  static const Color amber = Color(0xFFB08A3E); // 警告 / 中等 / 模糊
  static const Color clay = Color(0xFFB04A3A); // 错误 / 严重（无 context 处使用）
}

/// 设计令牌：圆角 / 间距 / 阴影，供各页面统一取用。
class AppTokens {
  AppTokens._();

  // 圆角（整体收敛，古典克制）
  static const double radiusSm = 6;
  static const double radiusMd = 10;
  static const double radiusLg = 12;
  static const double radiusXl = 16;
  static const double radiusXxl = 22;

  // 间距
  static const double gapXs = 4;
  static const double gapSm = 8;
  static const double gapMd = 12;
  static const double gapLg = 16;
  static const double gapXl = 24;
  static const double gapXxl = 32;

  /// 近乎不可见的单层浅影（气泡、卡片等）
  static List<BoxShadow> softShadow(ColorScheme scheme) => [
        BoxShadow(
          color: scheme.shadow.withValues(alpha: 0.05),
          blurRadius: 8,
          offset: const Offset(0, 2),
        ),
      ];
}

TextTheme _refineTextTheme(TextTheme base, ColorScheme scheme) {
  // 展示字体：HarmonyOS Sans SC 只承担标题层级，正文保持系统字体
  const displayFont = 'HarmonyOS Sans SC';
  return base.copyWith(
    headlineMedium: base.headlineMedium?.copyWith(
      fontFamily: displayFont,
      fontWeight: FontWeight.w700,
      letterSpacing: 0.2,
    ),
    headlineSmall: base.headlineSmall?.copyWith(
      fontFamily: displayFont,
      fontWeight: FontWeight.w700,
      letterSpacing: 0.15,
    ),
    titleLarge: base.titleLarge?.copyWith(
      fontFamily: displayFont,
      fontWeight: FontWeight.w700,
      fontSize: 20,
      letterSpacing: 0.15,
    ),
    titleMedium: base.titleMedium?.copyWith(
      fontWeight: FontWeight.w600,
      letterSpacing: 0.15,
    ),
    titleSmall: base.titleSmall?.copyWith(
      fontWeight: FontWeight.w600,
      letterSpacing: 0.1,
    ),
    bodyLarge: base.bodyLarge?.copyWith(height: 1.5),
    bodyMedium: base.bodyMedium?.copyWith(height: 1.5),
    bodySmall: base.bodySmall?.copyWith(
      height: 1.45,
      color: scheme.onSurfaceVariant,
    ),
    labelSmall: base.labelSmall?.copyWith(
      color: scheme.onSurfaceVariant,
      letterSpacing: 0.2,
    ),
  );
}

ThemeData _build(Color seed, Brightness brightness) {
  final generated = ColorScheme.fromSeed(seedColor: seed, brightness: brightness);
  final isLight = brightness == Brightness.light;
  // Claude 式纸面：米白底、暖白卡、墨褐文字、暖灰 hairline，强调色收敛为赤陶
  final scheme = isLight
      ? generated.copyWith(
          surface: AppPalette.paper,
          surfaceContainerLowest: AppPalette.cardLight,
          surfaceContainerLow: const Color(0xFFFAF6EF),
          surfaceContainer: const Color(0xFFF1ECE3),
          surfaceContainerHigh: const Color(0xFFECE6DB),
          surfaceContainerHighest: const Color(0xFFE6DFD3),
          onSurface: AppPalette.ink,
          onSurfaceVariant: AppPalette.inkSoft,
          outlineVariant: AppPalette.hairlineLight,
          outline: const Color(0xFFC9BFAD),
        )
      : generated.copyWith(
          surface: AppPalette.soot,
          surfaceContainerLowest: const Color(0xFF16130F),
          surfaceContainerLow: const Color(0xFF211C16),
          surfaceContainer: AppPalette.cardDark,
          surfaceContainerHigh: const Color(0xFF322B20),
          surfaceContainerHighest: const Color(0xFF3D3529),
          onSurface: AppPalette.paperInk,
          onSurfaceVariant: const Color(0xFFA79C8C),
          outlineVariant: AppPalette.hairlineDark,
          outline: const Color(0xFF5C5344),
        );
  // 背景与卡片拉开层次：浅色模式纸底暖白卡，深色模式深底浅卡
  final scaffoldColor =
      isLight ? scheme.surface : scheme.surface;
  final cardColor =
      isLight ? scheme.surfaceContainerLowest : scheme.surfaceContainerLow;
  final hairline = scheme.outlineVariant.withValues(alpha: isLight ? 0.85 : 0.7);

  final base = ThemeData(useMaterial3: true, colorScheme: scheme);
  final textTheme = _refineTextTheme(base.textTheme, scheme);

  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    textTheme: textTheme,
    scaffoldBackgroundColor: scaffoldColor,

    appBarTheme: AppBarTheme(
      centerTitle: true,
      elevation: 0,
      scrolledUnderElevation: 0.5,
      backgroundColor: scaffoldColor,
      surfaceTintColor: Colors.transparent,
      titleTextStyle: textTheme.titleLarge?.copyWith(
        fontSize: 18,
        color: scheme.onSurface,
      ),
      iconTheme: IconThemeData(color: scheme.onSurfaceVariant),
    ),

    cardTheme: CardThemeData(
      elevation: 0,
      color: cardColor,
      clipBehavior: Clip.antiAlias,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppTokens.radiusLg),
        side: BorderSide(color: hairline),
      ),
    ),

    dialogTheme: DialogThemeData(
      backgroundColor: scheme.surfaceContainerHigh,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppTokens.radiusXxl),
      ),
      titleTextStyle: textTheme.titleLarge?.copyWith(
        fontSize: 18,
        color: scheme.onSurface,
      ),
    ),

    bottomSheetTheme: BottomSheetThemeData(
      backgroundColor: scheme.surfaceContainerLow,
      surfaceTintColor: Colors.transparent,
      modalElevation: 2,
      clipBehavior: Clip.antiAlias,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(
          top: Radius.circular(AppTokens.radiusXxl),
        ),
      ),
    ),

    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: scheme.surfaceContainerHighest.withValues(alpha: 0.5),
      isDense: true,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      hintStyle: TextStyle(color: scheme.outline),
      labelStyle: TextStyle(color: scheme.onSurfaceVariant),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppTokens.radiusMd),
        borderSide: BorderSide.none,
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppTokens.radiusMd),
        borderSide: BorderSide.none,
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppTokens.radiusMd),
        borderSide: BorderSide(color: scheme.primary, width: 1.5),
      ),
      errorBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppTokens.radiusMd),
        borderSide: BorderSide(color: scheme.error),
      ),
      focusedErrorBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppTokens.radiusMd),
        borderSide: BorderSide(color: scheme.error, width: 1.5),
      ),
    ),

    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size(64, 48),
        elevation: 0,
        textStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppTokens.radiusMd),
        ),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size(64, 44),
        side: BorderSide(color: scheme.outlineVariant),
        textStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppTokens.radiusMd),
        ),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        textStyle: const TextStyle(fontWeight: FontWeight.w600),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppTokens.radiusMd),
        ),
      ),
    ),

    chipTheme: base.chipTheme.copyWith(
      backgroundColor: scheme.surfaceContainerHighest.withValues(alpha: 0.45),
      selectedColor: scheme.secondaryContainer,
      checkmarkColor: scheme.onSecondaryContainer,
      side: BorderSide(color: hairline),
      labelStyle: textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w500),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppTokens.radiusSm),
      ),
    ),

    navigationBarTheme: NavigationBarThemeData(
      height: 68,
      elevation: 0,
      backgroundColor: scaffoldColor,
      surfaceTintColor: Colors.transparent,
      indicatorColor: scheme.secondaryContainer,
      iconTheme: WidgetStateProperty.resolveWith(
        (states) => IconThemeData(
          color: states.contains(WidgetState.selected)
              ? scheme.onSecondaryContainer
              : scheme.onSurfaceVariant,
        ),
      ),
      labelTextStyle: WidgetStateProperty.resolveWith(
        (states) => TextStyle(
          fontSize: 12,
          fontWeight:
              states.contains(WidgetState.selected) ? FontWeight.w600 : FontWeight.w500,
          color: states.contains(WidgetState.selected)
              ? scheme.onSurface
              : scheme.onSurfaceVariant,
        ),
      ),
    ),

    tabBarTheme: TabBarThemeData(
      indicatorSize: TabBarIndicatorSize.tab,
      labelColor: scheme.primary,
      unselectedLabelColor: scheme.onSurfaceVariant,
      labelStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
      unselectedLabelStyle:
          const TextStyle(fontWeight: FontWeight.w500, fontSize: 14),
      dividerColor: hairline,
      indicator: UnderlineTabIndicator(
        borderSide: BorderSide(color: scheme.primary, width: 2.5),
        borderRadius: const BorderRadius.vertical(
          top: Radius.circular(AppTokens.radiusSm),
        ),
      ),
    ),

    snackBarTheme: SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppTokens.radiusMd),
      ),
    ),

    progressIndicatorTheme: ProgressIndicatorThemeData(
      color: scheme.primary,
      linearTrackColor: scheme.surfaceContainerHighest,
    ),

    dividerTheme: DividerThemeData(color: hairline, thickness: 1, space: 1),

    listTileTheme: ListTileThemeData(
      iconColor: scheme.onSurfaceVariant,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppTokens.radiusMd),
      ),
    ),

    segmentedButtonTheme: SegmentedButtonThemeData(
      style: ButtonStyle(
        textStyle: const WidgetStatePropertyAll(
          TextStyle(fontWeight: FontWeight.w500),
        ),
        shape: WidgetStatePropertyAll(
          RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AppTokens.radiusMd),
          ),
        ),
      ),
    ),

    // 展开面板去掉默认上下分隔线，视觉更干净
    expansionTileTheme: const ExpansionTileThemeData(
      shape: RoundedRectangleBorder(side: BorderSide.none),
      collapsedShape: RoundedRectangleBorder(side: BorderSide.none),
    ),

    popupMenuTheme: PopupMenuThemeData(
      elevation: 3,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppTokens.radiusLg),
      ),
    ),

    checkboxTheme: CheckboxThemeData(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(4)),
    ),
  );
}

/// 角色种子色：统一收敛为赤陶（terracotta）家族，仅按角色微调深浅。
/// 学生=标准赤陶、家长=暖杏陶、教师=深陶土
final appThemeProvider = Provider<AppTheme>((ref) {
  final mode = ref.watch(userModeProvider);
  final seed = switch (mode) {
    UserMode.teacher => const Color(0xFF8F4E38),
    UserMode.parent => const Color(0xFFB46940),
    UserMode.student => AppPalette.terracotta,
  };
  return AppTheme(
    light: _build(seed, Brightness.light),
    dark: _build(seed, Brightness.dark),
  );
});
