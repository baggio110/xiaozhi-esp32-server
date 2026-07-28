# 智控台家庭记忆设置

“家庭记忆”位于智控台超级管理员的“参数管理”下拉菜单中。页面默认存在，但 `family_memory.enabled` 默认是 `false`，安装或升级不会自动启用、创建成员或绑定声纹。

## 首次配置

1. 选择要管理的 xiaozhi-server WebSocket 地址。
2. 填写长期固定的 `family_id`。
3. 保持默认的 `database_path=data/family_identity.db`，除非部署卷使用了项目内的其他相对路径。
4. 点击“环境检查”，确认没有 `FAIL`。
5. 保存关闭状态下的设置。
6. 新增家庭成员；`person_id` 是稳定身份，显示名称可以修改或重复。
7. 绑定现有官方 `voiceprint_id`。输入 `agentId` 可以加载当前登录用户已有的官方声纹；列表不可用时，可手工输入官方声纹管理结果或接口响应中的 ID。
8. 再次执行环境检查，开启家庭记忆并保存。
9. 按页面提示重启 xiaozhi-server。

`family_id:person_id` 是 PowerMem 使用的稳定 `memory_user_id`。`speaker_name` 只用于显示，`voiceprint_id` 只是可更换的识别凭据。

## 环境检查

页面直接展示 server 端现有 `run_preflight()` 结果，包括 Python、PowerMem 版本及 `user_id` 接口、数据库目录和 schema、家庭 ID、成员数量、有效声纹数量以及 `PASS`、`WARN`、`FAIL` 汇总。存在 `FAIL` 时，manager-api 会拒绝保存启用状态。

## 安全边界

调用链固定为：

```text
manager-web
  → manager-api 登录鉴权和超级管理员权限
  → 一次性内部管理标记 + 设备 JWT + server.secret 校验 WebSocket 链路
  → xiaozhi-server 家庭记忆管理处理器
  → B6 管理操作和 SQLiteIdentityRepository
  → data/family_identity.db
```

浏览器不直接访问 server 或 SQLite，manager-api 也不打开 `family_identity.db`。server 仍是身份数据库的唯一写入者。接口不读取或删除 PowerMem 记忆，不返回用户画像、密钥或其他家庭的人员详情。

普通小智设备的 JWT 只代表设备连接身份，不代表管理身份。家庭记忆管理连接必须由 manager-api 通过一次性 Redis 标记创建，并在 server 端再次验证 `server.secret`；任一条件不满足都拒绝管理操作。

## 开关、重启与回滚

设置继续保存在 manager-api 的现有 `sys_params` 中：

- `family_memory.enabled`
- `family_memory.family_id`
- `family_memory.database_path`

配置保存在 manager-api 数据库卷中，身份映射保存在 xiaozhi-server 的 `data` 卷中。启用、关闭或修改配置后需要重启 xiaozhi-server；本页面不会控制 Docker 或群晖容器。

关闭家庭记忆后，官方 `device_id` 记忆行为恢复，已有官方设备记忆不会被删除。页面不提供人员物理删除、PowerMem 记忆删除或容器控制。

## 仍需实体部署验证

- 群晖实际 volume 映射和文件权限。
- 自定义镜像中的 PowerMem 真实版本与接口签名。
- 官方声纹服务生成的 `voiceprint_id` 与实际识别返回值一致。
- 双终端并发、断网重连和容器重启后的完整端到端行为。
