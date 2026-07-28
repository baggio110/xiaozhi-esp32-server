# 回滚说明

1. 禁用家庭记忆并停止两个定制容器。
2. 保留并备份 `family_identity.db`；不得为了回滚删除个人身份映射。
3. 将 `FAMILY_MEMORY_SERVER_IMAGE` 和 `FAMILY_MEMORY_WEB_IMAGE` 恢复为
   升级前记录的官方固定镜像名称或镜像 ID。
4. 移除 `compose.family-memory.yml`，或只使用原
   `docker-compose_all.yml` 重新创建两个应用容器。
5. 不修改 MySQL、Redis、data、uploadfile、端口、网络和密码。
6. 确认官方 server 重新使用原 `device_id` 记忆行为。
7. 保留原 `device_id` 记忆和 `family_identity.db`，以便将来重新升级。

若回滚原因是数据异常，先保留现场副本，再从升级前一致性备份恢复；不要直接
覆盖仍在使用的数据库文件。
