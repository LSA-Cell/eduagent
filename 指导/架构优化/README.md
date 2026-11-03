# 架构优化

- [项目说明与原根 README 正文](项目说明.md)：技术栈、运行、测试、端口与兼容修复。文中目录树为整理前布局记录，当前文档位置以[总索引](../README.md)为准。
- [总体架构与服务边界](architecture.md)
- [编排集成说明](orchestration_integration.md)
- [项目开发现状与路线图](项目开发现状与路线图.md)：保留原开发与验证结论，不等同本次重新验收。
- [SSL 证书部署说明](部署/SSL证书说明.txt)：证书仍放在 infra/nginx/certs 原目录，不将私钥归入指导。

## 历史安全审计

- [2026-08-17 审计](安全审计/security_audit_2026-08-17.md)
- [2026-08-18 复扫](安全审计/security_audit_2026-08-18.md)
- [2026-08-18 访客鉴权复核](安全审计/security_audit_2026-08-18_guest-auth.md)
- 原始机读报告：[security_report.json](安全审计/security_report.json)、[security_scan_full.json](安全审计/security_scan_full.json)、[security_owasp.json](安全审计/security_owasp.json)。

审计 JSON 原样移动，保留历史扫描路径和证据，不把历史零命中当成本轮复扫。持久化细节见[数据库设计](../数据库设计/README.md)，后端提示词约束见[后端设计](../后端设计/README.md)。

[返回总索引](../README.md)
