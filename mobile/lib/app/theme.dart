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

/// 设计令牌：圆角 / 间距 / 阴影 / 渐变，供各页面统一取用。
class AppTokens {
  AppTokens._();

  // 圆角
  static const double radiusSm = 8;
  static const double radiusMd = 12;
  static const double radiusLg = 16;
  static const double radiusXl = 20;
  static const double radiusXxl = 28;

  // 间距
  static const double gapXs = 4;
  static const double gapSm = 8;
  static const double gapMd = 12;
  static const double gapLg = 16;
  static const double gapXl = 24;
  static const double gapXxl = 32;

  /// 柔和浮起阴影（气泡、悬浮按钮等）
  static List<BoxShadow> softShadow(ColorScheme scheme) => [
        BoxShadow(
          color: scheme.shadow.withValues(alpha: 0.08),
          blurRadius: 12,
          offset: const Offset(0, 3),
        ),
      ];

  /// 品牌色光晕（录音按钮、登录 Logo 等强调元素）
  static List<BoxShadow> glowShadow(Color color) => [
        BoxShadow(
          color: color.withValues(alpha: 0.35),
          blurRadius: 20,
          offset: const Offset(0, 6),
        ),
      ];

  /// 品牌渐变：primary → tertiary（用户气泡、Logo、主视觉按钮）
  static LinearGradient brandGradient(ColorScheme scheme) => LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [scheme.primary, scheme.tertiary],
      );

  /// 头像/图标容器的浅色渐变底
  static LinearGradient tintedGradient(ColorScheme scheme) => LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [
          scheme.primaryContainer.withValues(alpha: 0.65),
          scheme.tertiaryContainer.withValues(alpha: 0.45),
        ],
      );
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
  final scheme = ColorScheme.fromSeed(seedColor: seed, brightness: brightness);
  final isLight = brightness == Brightness.light;
  // 背景与卡片拉开层次：浅色模式灰底白卡，深色模式深底浅卡
  final scaffoldColor =
      isLight ? scheme.surfaceContainerLow : scheme.surface;
  final cardColor =
      isLight ? scheme.surfaceContainerLowest : scheme.surfaceContainerLow;
  final hairline = scheme.outlineVariant.withValues(alpha: isLight ? 0.55 : 0.4);

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

/// 角色种子色（避开 Material 官方色板色号，取自教育场景的物件）：
/// 学生=墨水蓝（钢笔水）、家长=暖杏色（宣纸/木质课桌）、教师=松绿（黑板）
final appThemeProvider = Provider<AppTheme>((ref) {
  final mode = ref.watch(userModeProvider);
  final seed = switch (mode) {
    UserMode.teacher => const Color(0xFF1F6F5C),
    UserMode.parent => const Color(0xFFC2703D),
    UserMode.student => const Color(0xFF2B50AA),
  };
  return AppTheme(
    light: _build(seed, Brightness.light),
    dark: _build(seed, Brightness.dark),
  );
});
