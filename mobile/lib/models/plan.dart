/// 学习计划任务
class PlanTask {
  const PlanTask({
    required this.id,
    required this.title,
    required this.done,
    this.minutes = 30,
  });

  final String id;
  final String title;
  final bool done;

  /// 预计用时（分钟）
  final int minutes;

  PlanTask copyWith({bool? done}) => PlanTask(
        id: id,
        title: title,
        done: done ?? this.done,
        minutes: minutes,
      );

  factory PlanTask.fromJson(Map<String, dynamic> json) => PlanTask(
        id: json['id'] as String,
        title: json['title'] as String? ?? '',
        done: json['done'] as bool? ?? false,
        minutes: json['minutes'] as int? ?? 30,
      );

  Map<String, dynamic> toJson() =>
      {'id': id, 'title': title, 'done': done, 'minutes': minutes};
}

/// 学习计划
class StudyPlan {
  const StudyPlan({
    required this.id,
    required this.title,
    required this.subject,
    required this.tasks,
    this.deadline,
  });

  final String id;
  final String title;
  final String subject;
  final List<PlanTask> tasks;
  final DateTime? deadline;

  /// 完成进度 0~1
  double get progress {
    if (tasks.isEmpty) return 0;
    return tasks.where((t) => t.done).length / tasks.length;
  }

  StudyPlan copyWith({List<PlanTask>? tasks}) => StudyPlan(
        id: id,
        title: title,
        subject: subject,
        tasks: tasks ?? this.tasks,
        deadline: deadline,
      );

  /// 对齐后端 Agent plan_to_dict：generated_schedule [{day, task, duration}]
  /// 映射为任务列表，completed_days 命中即为已完成；task.id 取 day 序号
  ///（打卡接口 POST /study-plans/{id}/checkin 入参即 day）。
  factory StudyPlan.fromJson(Map<String, dynamic> json) {
    final completed = (json['completed_days'] as List<dynamic>?)
            ?.map((e) => (e as num).toInt())
            .toSet() ??
        const <int>{};
    final schedule = json['generated_schedule'] as List<dynamic>? ?? const [];
    return StudyPlan(
      id: json['id'] as String,
      title: (json['title'] ?? json['goal_description']) as String? ?? '',
      subject: json['subject'] as String? ?? '', // 后端计划暂无学科字段
      tasks: [
        for (final item in schedule)
          if (item is Map<String, dynamic>)
            PlanTask(
              id: '${item['day']}',
              title: item['task'] as String? ?? '',
              done: completed.contains((item['day'] as num?)?.toInt()),
              minutes: (item['duration'] as num?)?.toInt() ?? 30,
            ),
      ],
      deadline:
          DateTime.tryParse((json['end_date'] ?? json['deadline']) as String? ?? ''),
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'title': title,
        'subject': subject,
        'tasks': tasks.map((e) => e.toJson()).toList(),
        if (deadline != null) 'deadline': deadline!.toIso8601String(),
      };
}
