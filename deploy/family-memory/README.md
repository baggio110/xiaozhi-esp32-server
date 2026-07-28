# 家庭记忆安装包

本安装包保持官方全模块部署结构：一个 `server` 镜像，加一个合并
manager-api、manager-web 与 Nginx 的 `web` 镜像。当前基于官方
`v0.9.6`，验证 PowerMem `0.5.3`，支持 `linux/amd64`，适用于
Synology DS920+ 等 x86-64 设备。

本轮 GitHub Actions 只验证镜像，不发布镜像。只有后续创建符合规则的
版本标签并且发布工作流全部通过后，才能使用对应 GHCR 固定标签。

## 10分钟快速开始

1. **确认版本**：只在官方服务端 `v0.9.6` 基线上使用。镜像标签必须是
   `v0.9.6-family-memory.N` 形式，不使用浮动标签。
2. **备份**：停止写入后备份整个 `data`、MySQL 数据库、`uploadfile`
   和 PowerMem 后端。PowerMem 使用 SQLite 时备份实际数据库文件；
   使用外部数据库时采用该数据库的原生一致性备份。
3. **记录旧镜像**：

   ```bash
   docker inspect xiaozhi-esp32-server --format '{{.Config.Image}} {{.Image}}'
   docker inspect xiaozhi-esp32-server-web --format '{{.Config.Image}} {{.Image}}'
   ```

4. **下载固定提交的覆盖文件**：从成功的
   `Family Memory Images CI` 运行记录取得完整 B9 commit，禁止使用未验证分支：

   ```bash
   B9_COMMIT=<通过CI的完整提交哈希>
   curl -fL -o compose.family-memory.yml \
     "https://raw.githubusercontent.com/baggio110/xiaozhi-esp32-server/${B9_COMMIT}/deploy/family-memory/compose.family-memory.yml"
   ```

5. **设置固定镜像**：

   ```bash
   export FAMILY_MEMORY_SERVER_IMAGE=ghcr.io/baggio110/xiaozhi-family-memory-server:v0.9.6-family-memory.1
   export FAMILY_MEMORY_WEB_IMAGE=ghcr.io/baggio110/xiaozhi-family-memory-web:v0.9.6-family-memory.1
   ```

6. **核对合并结果**：覆盖文件必须与现有
   `docker-compose_all.yml` 一起使用。确认 data、MySQL、uploadfile、端口和网络均未改变：

   ```bash
   docker compose -f docker-compose_all.yml -f compose.family-memory.yml config
   ```

7. **首次保持家庭记忆关闭**：升级镜像后先确认
   `family_memory.enabled=false`，不要删除或移动现有 data。
8. **进入智控台**：用超级管理员登录，打开“家庭记忆”，填写永久
   `family_id` 和项目内数据库路径 `data/family_identity.db`，先运行环境检查。
9. **建立绑定**：新增任意家庭成员，再将智控台现有官方声纹 ID 绑定到该成员。
   `speaker_name` 只用于显示，不能作为身份主键。
10. **启用并重启**：环境检查没有 FAIL 后启用家庭记忆，然后重启
    `xiaozhi-esp32-server`；配置不会在保存瞬间热加载。
11. **验证隔离**：让成员甲在终端一保存记忆、在终端二读取；成员乙和匿名人员
    必须无法读取或写入成员甲的私人记忆。
12. **保留旧数据**：不得删除 `family_identity.db`，也不得删除原有基于
    `device_id` 的记忆；家庭功能关闭时仍使用官方兼容路径。
13. **恢复官方镜像**：按 [ROLLBACK.md](ROLLBACK.md) 恢复第3步记录的
    两个镜像，并移除覆盖文件。

> 必须人工核对群晖上真正使用的 data 挂载路径。不要根据示例目录猜测。

升级步骤见 [UPGRADE.md](UPGRADE.md)，回滚步骤见
[ROLLBACK.md](ROLLBACK.md)，兼容范围见
[COMPATIBILITY.md](COMPATIBILITY.md)。
