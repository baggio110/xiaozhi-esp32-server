# 升级说明

1. 确认目标镜像标签对应成功的 `Family Memory Images CI` 和标签发布工作流。
2. 记录当前两个容器的镜像名称、固定标签和镜像 ID。
3. 停止业务写入，备份：
   - 实际挂载的 `data`；
   - MySQL；
   - `uploadfile`；
   - PowerMem 实际使用的 SQLite 文件或外部数据库。
4. 不删除 `family_identity.db`，不迁移或删除原 `device_id` 记忆。
5. 设置两个新的固定镜像变量。
6. 执行 `docker compose ... config`，逐项确认卷、端口、网络、环境变量和
   容器名与升级前一致。
7. 仅重建 `xiaozhi-esp32-server` 和 `xiaozhi-esp32-server-web`；
   不重建 MySQL、Redis，不修改数据卷。
8. 首次保持家庭记忆关闭，确认智控台与 server 正常后执行 preflight。
9. preflight 无 FAIL 后启用功能并重启 server。
10. 执行跨终端、跨人员、匿名和低可信度隔离验收。

本安装包不执行数据库自动迁移，也不替用户推断 data 或 PowerMem 的真实位置。
