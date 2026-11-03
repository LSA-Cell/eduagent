# 后端设计

## 设计提示词

- [EduAgent 编排层设计 Master Prompt](提示词/EduAgent_编排层设计_Master_Prompt.txt)
- [EduAgent 增强机制设计 Master Prompt](提示词/EduAgent_增强机制设计_Master_Prompt.txt)
- [运行时提示词索引](运行时提示词索引.md)：集中索引被程序直接加载的 YAML/源码，保留原运行路径。

## 协议与服务

- [HTTP 接口规范](API协议/openapi.md)
- [客户端与后端路径契约](API协议/contract.md)
- [AsyncAPI / SSE 事件协议](API协议/asyncapi.yaml)
- [Billing 设计规范](计费/spec.md)
- 服务说明：[BFF](服务说明/bff.md)、[推理网关](服务说明/gateway.md)、[用户 API Key 代理](服务说明/proxy.md)。文中命令仍按原 backend 服务目录执行。

服务职责与集成约束见[总体架构](../架构优化/architecture.md)和[编排集成](../架构优化/orchestration_integration.md)；持久化权威位置见[数据库设计](../数据库设计/README.md)。

[返回总索引](../README.md)

## 待实施功能方案

[“我的”页面功能实现方案](../前端设计/我的页面功能实现方案.md#5-后端与接口职责方案)：资料、历史、收藏、偏好、反馈与导出的职责建议；尚未新增后端实现。
