# 数据库设计

[数据库现状与权威位置](数据库现状与权威位置.md)是本次整理新增的事实索引，不是数据库改造或执行记录。

- [首次初始化 DDL、索引与 RLS](../../infra/postgres/init.sql)
- [已有数据库兼容补丁](../../infra/postgres/compat_orm_columns.sql)
- [SQLAlchemy 共享 ORM](../../backend/shared/eduagent_shared/models.py)
- [隔离测试库结构契约验证脚本](../../backend/scripts/verify_schema_contract.py)
- [项目说明中的兼容修复步骤](../架构优化/项目说明.md#已有数据库卷的兼容修复)
- [Billing 数据领域规范](../后端设计/计费/spec.md)

可执行 SQL 保留在 infra/postgres，避免破坏 Compose 初始化路径；源码模型保留在共享包。本次没有连接或更改数据库。

[返回总索引](../README.md)

## 待实施结构方案

[个人空间数据库候选](../前端设计/我的页面功能实现方案.md#6-数据库设计候选)：历史复用、收藏唯一性和偏好字段候选；本轮未修改或执行 SQL。
