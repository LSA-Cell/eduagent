import 'model_utils.dart';

/// 用户身份
enum UserMode { student, teacher, parent }

/// 用户资料
class UserProfile {
  const UserProfile({
    required this.id,
    required this.nickname,
    required this.mode,
    this.grade,
    this.subjects = const [],
    this.avatarUrl,
    this.teacherVerified = false,
    this.selectedModel = 'auto',
  });

  final String id;
  final String nickname;
  final UserMode mode;

  /// 年级，如 "高一" / "大三"
  final String? grade;

  /// 学科列表
  final List<String> subjects;
  final String? avatarUrl;

  /// 教师是否已通过认证
  final bool teacherVerified;

  /// 当前选择的模型（auto 表示双轨路由自动选择）
  final String selectedModel;

  UserProfile copyWith({
    String? nickname,
    UserMode? mode,
    String? grade,
    List<String>? subjects,
    String? avatarUrl,
    bool? teacherVerified,
    String? selectedModel,
  }) {
    return UserProfile(
      id: id,
      nickname: nickname ?? this.nickname,
      mode: mode ?? this.mode,
      grade: grade ?? this.grade,
      subjects: subjects ?? this.subjects,
      avatarUrl: avatarUrl ?? this.avatarUrl,
      teacherVerified: teacherVerified ?? this.teacherVerified,
      selectedModel: selectedModel ?? this.selectedModel,
    );
  }

  /// 兼容后端 UserOut（user_mode/grade_level/default_subject/is_teacher）
  /// 与本地缓存的 camelCase 键；nickname/avatarUrl 服务端暂无字段，仅本地保留。
  factory UserProfile.fromJson(Map<String, dynamic> json) {
    final defaultSubject = json['default_subject'] as String?;
    return UserProfile(
      id: json['id'] as String,
      nickname: json['nickname'] as String? ?? '',
      mode: enumByName(UserMode.values,
          (json['user_mode'] ?? json['mode']) as String?, UserMode.student),
      grade: (json['grade_level'] ?? json['grade']) as String?,
      subjects: (json['subjects'] as List<dynamic>?)
              ?.map((e) => e as String)
              .toList() ??
          [if (defaultSubject != null) defaultSubject],
      avatarUrl: json['avatarUrl'] as String?,
      teacherVerified:
          (json['is_teacher'] ?? json['teacherVerified']) as bool? ?? false,
      selectedModel: json['selectedModel'] as String? ?? 'auto',
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'nickname': nickname,
        'mode': mode.name,
        if (grade != null) 'grade': grade,
        'subjects': subjects,
        if (avatarUrl != null) 'avatarUrl': avatarUrl,
        'teacherVerified': teacherVerified,
        'selectedModel': selectedModel,
      };
}

/// 服务端返回的 API Key 条目（仅掩码，永不包含明文；与 BFF ApiKeyOut 对齐）
class ApiKeyEntry {
  const ApiKeyEntry({
    required this.id,
    required this.provider,
    required this.maskedKey,
    required this.createdAt,
  });

  /// 记录 id（删除时按 id 传参：DELETE /users/me/api-keys/{id}）
  final String id;

  final String provider;

  /// 掩码形式，如 sk-****abcd
  final String maskedKey;
  final DateTime createdAt;

  factory ApiKeyEntry.fromJson(Map<String, dynamic> json) => ApiKeyEntry(
        id: json['id'] as String? ?? '',
        provider: json['provider'] as String,
        maskedKey: (json['masked_key'] ?? json['maskedKey']) as String? ?? '****',
        createdAt: DateTime.tryParse(
                (json['created_at'] ?? json['createdAt']) as String? ?? '') ??
            DateTime.now(),
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'provider': provider,
        'maskedKey': maskedKey,
        'createdAt': createdAt.toIso8601String(),
      };
}
