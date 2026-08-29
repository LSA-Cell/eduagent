import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:image_picker/image_picker.dart';

import '../../../app/theme.dart';
import '../../../models/user.dart';
import '../../profile/profile_provider.dart';
import '../providers/chat_provider.dart';
import 'tool_sheets.dart';

/// 工具点击行为分类
enum EduToolBehavior {
  /// 上传文件类（拍照答疑/作业批改/文档解析）：底部上传 Sheet
  upload,

  /// 直接触发类：弹参数收集 Sheet，提交后在聊天区插入系统消息并调用
  direct,

  /// 路由跳转类（口语对练/错题本/记忆卡片）
  route,

  /// 开关类（联网搜索）：切换状态并高亮
  toggle,
}

/// 教育工具定义
class EduTool {
  const EduTool({
    required this.id,
    required this.label,
    required this.icon,
    required this.behavior,
    this.route,
    this.teacherPriority = false,
    this.teacherOnly = false,
  });

  final String id;
  final String label;
  final IconData icon;
  final EduToolBehavior behavior;

  /// behavior == route 时的跳转路径
  final String? route;

  /// 教师模式展开态优先展示
  final bool teacherPriority;

  /// 仅教师可用（学生模式置灰）
  final bool teacherOnly;
}

/// 全部 16 个工具（学生视角的默认顺序）
const List<EduTool> kEduTools = [
  EduTool(
      id: 'photo_qa',
      label: '拍照答疑',
      icon: Icons.photo_camera,
      behavior: EduToolBehavior.upload),
  EduTool(
      id: 'grade_homework',
      label: '作业批改',
      icon: Icons.fact_check,
      behavior: EduToolBehavior.upload),
  EduTool(
      id: 'parse_document',
      label: '文档解析',
      icon: Icons.description,
      behavior: EduToolBehavior.upload),
  EduTool(
      id: 'voice_practice',
      label: '口语对练',
      icon: Icons.record_voice_over,
      behavior: EduToolBehavior.route,
      route: '/voice'),
  EduTool(
      id: 'formula',
      label: '公式助手',
      icon: Icons.functions,
      behavior: EduToolBehavior.direct),
  EduTool(
      id: 'chemistry',
      label: '化学方程式',
      icon: Icons.science,
      behavior: EduToolBehavior.direct),
  EduTool(
      id: 'knowledge_check',
      label: '知识点检测',
      icon: Icons.quiz,
      behavior: EduToolBehavior.direct),
  EduTool(
      id: 'study_plan',
      label: '学习计划',
      icon: Icons.event_note,
      behavior: EduToolBehavior.direct),
  EduTool(
      id: 'mistakes',
      label: '错题本',
      icon: Icons.bookmark,
      behavior: EduToolBehavior.route,
      route: '/mistakes'),
  EduTool(
      id: 'flashcards',
      label: '记忆卡片',
      icon: Icons.style,
      behavior: EduToolBehavior.route,
      route: '/flashcards'),
  EduTool(
      id: 'essay_coach',
      label: '作文辅导',
      icon: Icons.edit_note,
      behavior: EduToolBehavior.direct),
  EduTool(
      id: 'web_search',
      label: '联网搜索',
      icon: Icons.travel_explore,
      behavior: EduToolBehavior.toggle),
  EduTool(
      id: 'make_ppt',
      label: '制作PPT',
      icon: Icons.slideshow,
      behavior: EduToolBehavior.direct,
      teacherPriority: true,
      teacherOnly: true),
  EduTool(
      id: 'make_lesson_plan',
      label: '制作教案',
      icon: Icons.menu_book,
      behavior: EduToolBehavior.direct,
      teacherPriority: true,
      teacherOnly: true),
  EduTool(
      id: 'make_exam',
      label: '生成试卷',
      icon: Icons.assignment,
      behavior: EduToolBehavior.direct,
      teacherPriority: true,
      teacherOnly: true),
  EduTool(
      id: 'analytics',
      label: '学情分析',
      icon: Icons.insights,
      behavior: EduToolBehavior.direct,
      teacherPriority: true,
      teacherOnly: true),
];

/// 身份驱动重排：教师模式将教师工具（PPT/教案/试卷/学情分析）排到最前。
/// 用稳定分区保持同组内相对顺序。
final orderedToolsProvider = Provider<List<EduTool>>((ref) {
  final mode = ref.watch(userModeProvider);
  if (mode == UserMode.teacher) {
    return [
      ...kEduTools.where((t) => t.teacherPriority),
      ...kEduTools.where((t) => !t.teacherPriority),
    ];
  }
  return kEduTools;
});

/// 教育工具栏：位于输入框与消息列表之间。
/// 折叠态 56dp（前 4 个常用工具 + 展开按钮）；
/// 展开态 220dp（16 个工具网格 + 收起按钮）；AnimatedContainer 200ms ease-out。
class EduToolbar extends ConsumerStatefulWidget {
  const EduToolbar({super.key});

  static const double collapsedHeight = 56;
  static const double expandedHeight = 220;

  @override
  ConsumerState<EduToolbar> createState() => _EduToolbarState();
}

class _EduToolbarState extends ConsumerState<EduToolbar> {
  bool _expanded = false;

  bool _enabled(EduTool tool, UserMode mode) =>
      !tool.teacherOnly || mode == UserMode.teacher;

  @override
  Widget build(BuildContext context) {
    final mode = ref.watch(userModeProvider);
    final tools = ref.watch(orderedToolsProvider);
    final webSearchOn =
        ref.watch(chatProvider.select((s) => s.webSearchEnabled));

    return AnimatedContainer(
      duration: const Duration(milliseconds: 200),
      curve: Curves.easeOut,
      height: _expanded ? EduToolbar.expandedHeight : EduToolbar.collapsedHeight,
      decoration: BoxDecoration(
        color: Theme.of(context).scaffoldBackgroundColor,
        border: Border(
            top: BorderSide(color: Theme.of(context).dividerColor, width: 0.5)),
      ),
      clipBehavior: Clip.hardEdge,
      child: _expanded
          ? _buildExpanded(tools, mode, webSearchOn)
          : _buildCollapsed(tools, mode, webSearchOn),
    );
  }

  /// 折叠态：前 4 个常用工具（图标按钮）+ 右侧"展开 ▼"
  Widget _buildCollapsed(List<EduTool> tools, UserMode mode, bool webSearchOn) {
    return Row(
      children: [
        const SizedBox(width: 8),
        for (final tool in tools.take(4))
          _ToolIconButton(
            tool: tool,
            enabled: _enabled(tool, mode),
            highlighted: tool.behavior == EduToolBehavior.toggle && webSearchOn,
            onTap: () => _onToolTap(tool),
          ),
        const Spacer(),
        TextButton.icon(
          onPressed: () => setState(() => _expanded = true),
          icon: const Icon(Icons.expand_more, size: 18),
          label: const Text('展开'),
        ),
        const SizedBox(width: 4),
      ],
    );
  }

  /// 展开态：全部 16 个工具网格（48dp 图标 + 10sp 标签）+ "收起 ▲"
  Widget _buildExpanded(List<EduTool> tools, UserMode mode, bool webSearchOn) {
    return Column(
      children: [
        Expanded(
          child: GridView.count(
            crossAxisCount: 4,
            childAspectRatio: 0.82,
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            physics: const BouncingScrollPhysics(),
            children: [
              for (final tool in tools)
                _ToolGridItem(
                  tool: tool,
                  enabled: _enabled(tool, mode),
                  highlighted:
                      tool.behavior == EduToolBehavior.toggle && webSearchOn,
                  onTap: () => _onToolTap(tool),
                ),
            ],
          ),
        ),
        SizedBox(
          height: 32,
          child: TextButton.icon(
            onPressed: () => setState(() => _expanded = false),
            icon: const Icon(Icons.expand_less, size: 18),
            label: const Text('收起'),
          ),
        ),
      ],
    );
  }

  /// 点击行为分类分发
  void _onToolTap(EduTool tool) {
    final mode = ref.read(userModeProvider);
    if (!_enabled(tool, mode)) return;
    switch (tool.behavior) {
      case EduToolBehavior.upload:
        _showUploadSheet(tool);
      case EduToolBehavior.direct:
        // 参数收集 Sheet（公式/化学/作文/检测/计划/生成/学情），
        // 提交后由 chatProvider 插入“正在使用…”系统消息并调用
        openToolSheet(context, toolId: tool.id, toolLabel: tool.label);
      case EduToolBehavior.toggle:
        ref.read(chatProvider.notifier).toggleWebSearch();
      case EduToolBehavior.route:
        context.push(tool.route!);
    }
  }

  /// 需上传类工具：底部 Sheet 选择 拍照/相册/文件
  void _showUploadSheet(EduTool tool) {
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (sheetContext) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.photo_camera),
              title: const Text('拍照'),
              onTap: () => _pick(sheetContext, tool, _PickSource.camera),
            ),
            ListTile(
              leading: const Icon(Icons.photo_library),
              title: const Text('相册'),
              onTap: () => _pick(sheetContext, tool, _PickSource.gallery),
            ),
            ListTile(
              leading: const Icon(Icons.folder_open),
              title: const Text('文件'),
              onTap: () => _pick(sheetContext, tool, _PickSource.file),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _pick(
      BuildContext sheetContext, EduTool tool, _PickSource source) async {
    Navigator.of(sheetContext).pop();
    String? path;
    switch (source) {
      case _PickSource.camera:
        final x = await ImagePicker()
            .pickImage(source: ImageSource.camera, maxWidth: 2048);
        path = x?.path;
      case _PickSource.gallery:
        final x = await ImagePicker()
            .pickImage(source: ImageSource.gallery, maxWidth: 2048);
        path = x?.path;
      case _PickSource.file:
        final files = await FilePicker.pickFiles();
        path = files.isEmpty ? null : files.single.path;
    }
    if (path == null) return;
    await ref
        .read(chatProvider.notifier)
        .invokeToolWithFile(tool.id, tool.label, path);
  }
}

enum _PickSource { camera, gallery, file }

/// 折叠态工具图标按钮（48x48，无标签以适配 56dp 高度）
class _ToolIconButton extends StatelessWidget {
  const _ToolIconButton({
    required this.tool,
    required this.enabled,
    required this.highlighted,
    required this.onTap,
  });

  final EduTool tool;
  final bool enabled;
  final bool highlighted;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final active = highlighted && enabled;
    return SizedBox(
      width: 48,
      height: 48,
      child: IconButton(
        onPressed: enabled ? onTap : null,
        tooltip: tool.label,
        icon: Container(
          width: 40,
          height: 40,
          decoration: BoxDecoration(
            color: active
                ? scheme.primaryContainer
                : scheme.surfaceContainerHighest.withValues(alpha: 0.6),
            borderRadius: BorderRadius.circular(AppTokens.radiusMd),
          ),
          child: Icon(
            tool.icon,
            size: 20,
            color: !enabled
                ? scheme.outline.withValues(alpha: 0.4) // 未启用置灰
                : active
                    ? scheme.primary
                    : scheme.onSurfaceVariant,
          ),
        ),
      ),
    );
  }
}

/// 展开态网格项：48x48 图标 + 10sp 标签
class _ToolGridItem extends StatelessWidget {
  const _ToolGridItem({
    required this.tool,
    required this.enabled,
    required this.highlighted,
    required this.onTap,
  });

  final EduTool tool;
  final bool enabled;
  final bool highlighted;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final active = highlighted && enabled;
    final color = !enabled
        ? scheme.outline.withValues(alpha: 0.4)
        : active
            ? scheme.primary
            : scheme.onSurfaceVariant;
    return InkWell(
      onTap: enabled ? onTap : null,
      borderRadius: BorderRadius.circular(AppTokens.radiusMd),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Container(
            width: 48,
            height: 48,
            decoration: BoxDecoration(
              color: active
                  ? scheme.primaryContainer
                  : enabled
                      ? scheme.surfaceContainerLow
                      : scheme.surfaceContainerHighest.withValues(alpha: 0.5),
              borderRadius: BorderRadius.circular(AppTokens.radiusMd + 2),
            ),
            child: Icon(tool.icon, color: color, size: 22),
          ),
          const SizedBox(height: 4),
          Text(
            tool.label,
            style: TextStyle(fontSize: 10, color: color, height: 1.1),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }
}
