# 家庭身份与个人记忆设计

## 范围

阶段 A 建立数据模型、接口契约、失败关闭策略和自动化测试。

阶段 B1 增加独立 SQLite 身份映射和 `IdentityService`，仍不接入现有业务调用链。

## 行为变化

当前官方行为：

```text
device_id → PowerMem user_id
```

目标行为：

```text
voiceprint_id → person_id → memory_user_id → PowerMem
```

其中：

```text
memory_user_id = family_id + ":" + person_id
```

## ID 职责

- `family_id`：稳定的家庭边界。
- `person_id`：稳定的家庭成员身份，更换声纹后保持不变。
- `voiceprint_id`：可更换、可撤销的声纹凭据，只用于查找人员。
- `speaker_name`：只用于显示和称呼，不参与身份主键。
- `device_id`：终端设备身份，不参与家庭模式的个人记忆主键。
- `memory_user_id`：传给 PowerMem 的个人隔离键，固定为 `family_id:person_id`。

## 安全策略

只有 `RECOGNIZED` 状态允许读取和写入私人记忆。

以下状态全部失败关闭，PowerMem 查询、画像和保存调用次数必须为零：

- `UNKNOWN_VOICEPRINT`
- `LOW_CONFIDENCE`
- `SERVICE_UNAVAILABLE`
- `PERSON_NOT_BOUND`
- `PERSON_DISABLED`
- `INVALID_RESULT`

失败时禁止回退到设备、显示名称、声纹凭据、上一次人员或默认成员。

`TurnIdentityContext` 是不可变的单轮身份快照。创建后不再读取连接对象中的当前说话人、最近一次识别人员或可变 `role_id`。

## 兼容策略

家庭模式关闭时继续使用官方原有 `device_id → PowerMem user_id` 行为。阶段 A 不修改该流程；阶段 B 通过功能开关增加旁路。

## SQLite 身份映射

阶段 B1 只使用两个表：

```text
family_person
├── family_id
├── person_id
├── display_name
├── enabled
├── created_at
└── updated_at

person_voiceprint
├── voiceprint_id
├── family_id
├── person_id
├── created_at
└── revoked_at
```

`family_person` 使用 `(family_id, person_id)` 复合主键。`voiceprint_id` 全局唯一，只能绑定一个人员。撤销操作保留记录并写入 `revoked_at`，已撤销凭据不能重新绑定给任何人员。

所有人员和声纹查询都必须显式传入 `family_id`。不同家庭即使提交相同 `voiceprint_id`，也不能跨家庭解析人员。

Repository 构造函数显式接收数据库文件路径。生产路径可配置为 `data/family_identity.db`；测试使用 `TemporaryDirectory` 内的临时文件。

每次 Repository 操作创建独立 SQLite 连接，并在操作结束后关闭：

- 每个连接启用外键；
- 所有输入使用参数化 SQL；
- 写操作使用 `BEGIN IMMEDIATE` 事务；
- 默认连接超时为 5 秒；
- 不跨线程共享连接；
- 不使用全局连接或连接池。

阶段 B1 不启用 WAL。当前生命周期是短连接、单服务进程和低频身份配置写入，默认 journal 模式足够。进入并发业务链路后应先测试真实读写压力，再决定是否启用 WAL。

当前 SQLite 结构版本为 `1`，保存在 `PRAGMA user_version`：

- 版本 `0` 的新数据库在同一建表事务中升级为版本 `1`；
- 版本 `1` 可重复初始化，不破坏已有数据；
- 高于当前支持版本的数据库会被明确拒绝，不允许静默降级；
- 后续任何结构变化必须通过明确迁移提升版本，不直接覆盖旧结构。

`IdentityService` 只执行：

```text
family_id + RecognitionResult
→ SQLiteIdentityRepository
→ IdentityPolicy
→ IdentityDecision
```

它不访问设备、连接对象、对话或 PowerMem，也不保存最近一次识别人员。

## B2a 配置与 Runtime

家庭记忆功能默认关闭。`FamilyMemorySettings` 严格只包含：

- `enabled: bool = false`
- `family_id: Optional[str] = None`
- `database_path: str = "data/family_identity.db"`

启用时必须显式配置非空 `family_id`；该值不会从设备、用户或智能体推断。配置模型只从调用方提供的 mapping 构造，不读取环境变量、真实配置或密钥。

`database_path` 必须是相对于 `xiaozhi-server` 根目录的路径。路径解析函数要求调用方显式传入绝对 `server_root`，拒绝绝对数据库路径和逃出项目根目录的目录穿越。路径解析本身不创建文件或目录。

`FamilyMemoryRuntime` 只在 `enabled=true` 且调用 `start()` 后解析路径、创建父目录并初始化 `SQLiteIdentityRepository`。重复 `start()` 幂等返回同一个 Repository。

`enabled=false` 时 Runtime 不创建 Repository、数据库或 `data` 目录，不生成 `person_id` 或 `memory_user_id`，也不改变官方 `device_id` 记忆行为。`close()` 只清除 Runtime 引用，不删除数据库或人员绑定。

B2a 尚未接入现有 `config.yaml`、配置加载器、声纹、连接、对话或 PowerMem 业务链路。

## 第一版不包含

- 智控台家庭成员页面
- manager-api 家庭管理
- `room_id` 与房间管理
- 家庭共享记忆
- Home Assistant 权限
- OceanBase
- Outbox 可靠队列
- 多实例协调
- 旧设备记忆迁移
