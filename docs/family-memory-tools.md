# 家庭成员映射与部署预检工具

## 工具定位

统一入口：

```text
python -m tools.family_memory.cli <command>
```

工具只管理家庭身份 SQLite 中的人员和声纹 ID 映射，并检查部署环境。官方声纹系统
负责注册、存储和识别声纹；本工具不录音、不上传音频、不提取声纹向量、不生成
`voiceprint_id`，也不调用或修改官方声纹服务。

`voiceprint_id` 必须从官方声纹管理结果或接口响应取得。当前仓库没有明确说明可复制
该 ID 的具体页面按钮，因此本文不假设页面名称或字段位置。

## 身份规则

- `family_id`：家庭长期稳定 ID，启用后不应随设备变化。
- `person_id`：家庭内长期稳定的机器标识。
- `display_name`：仅用于显示，可以重复。
- `voiceprint_id`：官方系统产生的可更换声纹凭据。
- `memory_user_id`：工具调用 `build_memory_user_id()` 生成
  `family_id:person_id`，不能由清单填写。

同一人员可以绑定多个不同的 `voiceprint_id`。`voiceprint_id` 在整个身份数据库中
全局唯一；撤销记录保留，已撤销 ID 不能直接重新绑定。

## JSON 初始化清单

```json
{
  "family_id": "home_001",
  "database_path": "data/family_identity.db",
  "members": [
    {
      "person_id": "grandfather_001",
      "display_name": "爷爷",
      "enabled": true,
      "voiceprint_ids": [
        "official_voiceprint_id_001"
      ]
    },
    {
      "person_id": "child_001",
      "display_name": "孩子",
      "enabled": true,
      "voiceprint_ids": []
    }
  ]
}
```

未知字段、重复 `person_id`、重复 `voiceprint_id`、空标识符和控制字符都会阻塞执行。
同名人员只要使用不同 `person_id`，就保持独立。

## plan：只读预览

```bash
python -m tools.family_memory.cli plan \
  --file family-members.json
```

机器可读输出：

```bash
python -m tools.family_memory.cli plan \
  --file family-members.json \
  --json
```

`plan` 显示新增、无变化、改名、启用、停用、声纹新增及冲突。它不会创建或修改
数据库，不会创建 WAL/SHM，也不会调用 PowerMem 或声纹服务。存在阻塞错误时退出码
非零。

## apply：显式确认后原子写入

```bash
python -m tools.family_memory.cli apply \
  --file family-members.json \
  --confirm-family-id home_001
```

`apply` 会重新执行完整 `plan`，并要求确认值与清单 `family_id` 精确一致。人员和新增
声纹绑定在一个 Repository 事务中应用；失败时本次数据变更整体回滚。重复执行相同
清单不会重复创建人员或声纹绑定。

清单缺失某个人员不会删除该人员，缺失某个既有声纹 ID 也不会自动撤销它。

## 查看人员

以下两种形式等价：

```bash
python -m tools.family_memory.cli list \
  --family-id home_001 \
  --database-path data/family_identity.db

python -m tools.family_memory.cli list persons \
  --family-id home_001 \
  --database-path data/family_identity.db \
  --json
```

输出包括人员 ID、显示名称、启用状态、`memory_user_id`、有效和已撤销声纹 ID。不会
输出 PowerMem 私人记忆、用户画像或密钥。

查看单人：

```bash
python -m tools.family_memory.cli person show \
  --family-id home_001 \
  --person-id child_001 \
  --database-path data/family_identity.db
```

## 新增、改名、启用和停用

```bash
python -m tools.family_memory.cli person upsert \
  --family-id home_001 \
  --person-id child_001 \
  --display-name 孩子 \
  --enabled true \
  --database-path data/family_identity.db \
  --confirm-family-id home_001

python -m tools.family_memory.cli person rename \
  --family-id home_001 \
  --person-id child_001 \
  --display-name 新显示名称 \
  --database-path data/family_identity.db \
  --confirm-family-id home_001

python -m tools.family_memory.cli person disable \
  --family-id home_001 \
  --person-id child_001 \
  --database-path data/family_identity.db \
  --confirm-family-id home_001

python -m tools.family_memory.cli person enable \
  --family-id home_001 \
  --person-id child_001 \
  --database-path data/family_identity.db \
  --confirm-family-id home_001
```

改名不改变 `person_id` 或 `memory_user_id`；停用不删除人员及其 PowerMem 长期记忆。
工具没有物理删除人员或清空个人记忆命令。

## 绑定、撤销和替换声纹

```bash
python -m tools.family_memory.cli voiceprint bind \
  --family-id home_001 \
  --person-id child_001 \
  --voiceprint-id official_voiceprint_id_002 \
  --database-path data/family_identity.db \
  --confirm-family-id home_001

python -m tools.family_memory.cli voiceprint revoke \
  --family-id home_001 \
  --voiceprint-id official_voiceprint_id_002 \
  --database-path data/family_identity.db \
  --confirm-family-id home_001

python -m tools.family_memory.cli voiceprint replace \
  --family-id home_001 \
  --person-id child_001 \
  --old-voiceprint-id official_voiceprint_id_002 \
  --new-voiceprint-id official_voiceprint_id_003 \
  --database-path data/family_identity.db \
  --confirm-family-id home_001
```

查看全部或单人的绑定：

```bash
python -m tools.family_memory.cli voiceprint list \
  --family-id home_001 \
  --database-path data/family_identity.db

python -m tools.family_memory.cli voiceprint list \
  --family-id home_001 \
  --person-id child_001 \
  --database-path data/family_identity.db \
  --json
```

绑定冲突会失败关闭，不会抢占其他人员的 ID。`replace` 复用 Repository 原子替换；
`revoke` 保留历史记录。

## preflight：部署配置预检

```bash
python -m tools.family_memory.cli preflight \
  --family-id home_001 \
  --database-path data/family_identity.db \
  --enabled true
```

JSON 输出：

```bash
python -m tools.family_memory.cli preflight \
  --family-id home_001 \
  --database-path data/family_identity.db \
  --enabled true \
  --json
```

预检检查：

- Python 版本和解释器路径；
- PowerMem 实际版本和模块位置；
- `AsyncMemory`、`UserMemory` 及 `search/profile/add`；
- 所需接口是否支持显式 `user_id`；
- `enabled`、`family_id`、`database_path`；
- 数据库是否位于 server 的 `data` 目录；
- 父目录访问能力；
- 已有身份库的 `PRAGMA user_version` 和两张预期表。

`PASS/WARN` 允许继续，存在 `FAIL` 时退出码非零；不适用项目使用 `SKIP`。数据库尚未
创建是 `WARN`，不会触发创建。PowerMem 高于已验证的 0.5.3 时会警告并继续检查接口
签名；低于 0.3.1 或缺少显式 `user_id` 时失败。

预检不会实例化 PowerMem 客户端，不调用 `search/profile/add`，也不读取私人记忆或
用户画像。

## Windows 示例

从 `F:\PythonXM\xiaozhi-esp32-server\main\xiaozhi-server` 执行：

```powershell
python -m tools.family_memory.cli plan `
  --file .\data\family-members.json

python -m tools.family_memory.cli preflight `
  --family-id home_001 `
  --database-path data/family_identity.db `
  --enabled true
```

显式绝对数据库路径也受支持，但文件名必须为 `family_identity.db`，且写命令不会自动
创建缺失的父目录。

## Linux 容器示例

从 `/opt/xiaozhi-esp32-server` 执行：

```bash
python -m tools.family_memory.cli preflight \
  --family-id home_001 \
  --database-path data/family_identity.db \
  --enabled true
```

当前已核验群晖环境的映射是：

```text
容器：/opt/xiaozhi-esp32-server/data/family_identity.db
宿主机：/volume1/docker/xiaozhi-server/data/family_identity.db
```

该宿主机路径仅适用于当前已核验的群晖部署。其他用户必须通过自己的 Docker volume
映射确认真实宿主机路径，不能直接照抄。
