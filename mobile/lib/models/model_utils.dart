/// 通用枚举解析辅助：按 name 匹配，失败回退默认值。
T enumByName<T extends Enum>(List<T> values, String? name, T fallback) {
  for (final v in values) {
    if (v.name == name) return v;
  }
  return fallback;
}
