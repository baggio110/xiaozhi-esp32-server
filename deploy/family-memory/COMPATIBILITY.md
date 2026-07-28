# 兼容性表

| 项目 | 状态 | 说明 |
|---|---|---|
| 官方服务端 | 支持 | 基于 `v0.9.6` |
| PowerMem | 支持 | 镜像内精确验证 `0.5.3` |
| 全模块部署 | 支持 | server + 合并 manager-api/manager-web 的 web 镜像 |
| `linux/amd64` | 支持 | GitHub Actions 构建验证目标；适配 DS920+ |
| `linux/arm64` | 未承诺 | 官方工作流与部署文档信息不一致，本轮不构建 |
| 家庭功能关闭 | 支持 | 保持官方 `device_id` 记忆兼容行为 |
| 多服务器/多进程 | 未支持 | 当前 SQLite 身份映射方案不覆盖 |
| PowerMem 并发能力 | 待部署压测 | 项目避免共享可变 user_id，但不虚构 SDK 线程安全 |

兼容结论只对通过对应提交 `Family Memory Images CI` 的固定镜像成立。
