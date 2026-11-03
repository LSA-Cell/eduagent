# EduAgent —— 教育领域 AI Agent

Flutter 移动端、FastAPI 服务与配套基础设施。项目说明、提示词和设计文档统一整理在[指导](指导/README.md)。

- [前端设计与 HTML 美化方案](指导/前端设计/README.md)
- [后端设计、提示词与 API 协议](指导/后端设计/README.md)
- [数据库设计与 SQL/ORM 索引](指导/数据库设计/README.md)
- [架构优化、部署与历史安全审计](指导/架构优化/README.md)
- [完整项目说明、运行及测试步骤](指导/架构优化/项目说明.md)
- [开发现状与验收路线图](指导/架构优化/项目开发现状与路线图.md)

本地视觉方案尚未上线。运行配置、依赖清单、SQL、源码及其运行时提示词保留原位置。

移动端直传文件时，服务端返回的 MinIO 签名地址必须可由设备访问。桌面模拟器可使用默认的
`MINIO_PUBLIC_ENDPOINT=localhost:9000`；Android 模拟器通常需要设为 `10.0.2.2:9000`，
真机需设为宿主机局域网地址，并将 `MINIO_PORT_BIND_IP` 设为相应网卡 IP。
生产环境应使用受 HTTPS 保护的对象存储域名，设置 `MINIO_PUBLIC_ENDPOINT` 和
`MINIO_PUBLIC_USE_SSL=true`，并通过独立入口接入 MinIO S3 API。签名地址中的主机名
必须与客户端实际访问的主机名一致。
