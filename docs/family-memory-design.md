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

## B2b 配置与服务生命周期接入

服务端配置模板增加 `family_memory` 节点，严格包含 `enabled`、`family_id` 和 `database_path` 三个字段。`app.main()` 通过现有 `load_config()` 获得配置字典；节点缺失时使用空 mapping，因此等同于家庭记忆关闭。

`start_family_memory_runtime()` 使用 `FamilyMemorySettings.from_mapping()` 解析节点，并以 `Path(__file__).resolve().parent` 作为稳定的 `xiaozhi-server` 根目录启动 `FamilyMemoryRuntime`。非法配置会记录不包含配置值的错误并中止启动，不会静默降级。

Runtime 在现有服务关闭 `finally` 中调用 `close()`。关闭状态不创建数据库，也不改变 WebSocket、HTTP、聊天或记忆模块的构造参数；启用状态只初始化身份 Repository，尚未向连接、声纹、对话或 PowerMem 传递 Repository。

## B3a 结构化声纹结果

`VoiceprintProvider.identify_speaker()` 返回阶段 A 的 `RecognitionResult`，保留声纹服务响应中的 `speaker_id`（映射为 `voiceprint_id`）、`score`（映射为 `confidence`）以及现有 `speaker_map` 中的显示名称。

响应映射遵循失败关闭原则：

- 有效声纹、达到现有阈值且存在名称映射：`RECOGNIZED`；
- 空或缺失 `speaker_id`：`UNKNOWN_VOICEPRINT`；
- 低于现有阈值：`LOW_CONFIDENCE`；
- 有效 `speaker_id` 没有本地名称映射：`PERSON_NOT_BOUND`；
- 超时、HTTP 错误或服务异常：`SERVICE_UNAVAILABLE`；
- 非 JSON、字段类型错误或无效结构：`INVALID_RESULT`。

可信度只由 `VoiceprintProvider` 使用当前连接实际取得的 `voiceprint.similarity_threshold` 分类一次。`RecognitionResult.status` 是后续身份解析的权威状态；`IdentityPolicy` 保留失败状态，并仅对 `RECOGNIZED` 的必要字段、人员绑定和启用状态进行校验，不使用另一套阈值重新分类。

`ASRProviderBase.handle_voice_stop()` 只从 `RECOGNIZED` 结果中提取非空 `speaker_name`，继续生成官方原有的 `{"speaker": "...", "content": "..."}` 输入。其他状态以及未启用声纹时仍使用原有非个人化聊天输入。本阶段不解析 `person_id`、不生成 `memory_user_id`，也不改变 PowerMem 的官方 `device_id` 行为。

## B3b 单轮身份上下文传递

`app.main()` 创建的唯一 `FamilyMemoryRuntime` 通过 `WebSocketServer` 构造参数显式传给每个 `ConnectionHandler`。启用时 Runtime 复用已有 `SQLiteIdentityRepository` 创建一个 `IdentityService`；关闭时不创建服务、不访问 Repository。

ASR 完成一轮识别后，将本轮 `RecognitionResult` 交给 Runtime 解析为 `IdentityDecision`，再使用连接既有的 `session_id`、`device_id` 和标准库生成的唯一 `turn_id` 创建不可变 `TurnIdentityContext`。该对象作为局部变量通过 `startToChat()` 的线程池任务直接传给 `ConnectionHandler.chat()`，不从连接上的当前说话人或历史身份反推。

所有非成功状态及解析异常都生成失败关闭决策，不回退到设备、显示名称、声纹凭据或上一次人员。家庭功能关闭时上下文为 `None`，原有聊天调用保持兼容。

本阶段 `chat()` 只接收并原样传递该参数，不使用它查询、修改或保存记忆；Dialogue、PowerMem、提示词和工具行为均保持不变。

## B4a1 连接级短期 Dialogue 存储

短期 Dialogue 在每个连接内部按已验证的 `memory_user_id` 隔离；同一人员在同一连接内复用同一个 Dialogue，不同人员使用不同 Dialogue。同一人员在不同连接中的短期 Dialogue 互不共享，跨终端共享只属于后续 PowerMem 长期记忆。

身份识别失败、`TurnIdentityContext` 为空以及没有声纹结果的入口统一使用该连接唯一的匿名 Dialogue。匿名 Dialogue 允许在同一连接内连续追问，但不读取或写入任何个人 PowerMem，不回退到设备或上一位人员，也不与个人 Dialogue 共享消息。

匿名 Dialogue 随连接级存储清理而释放，不持久化；匿名历史不得自动合并进个人历史，个人历史也不得复制到匿名 Dialogue。

本阶段仅实现独立的 `FamilySessionDialogueStore` 及其生命周期契约，尚未接入 `ConnectionHandler`。Dialogue 工厂如何复制 system prompt 和 few-shot 内容留待 B4a2。

## B4a2a Store 生命周期接入

每个家庭记忆 Runtime 已启动的 `ConnectionHandler` 独立持有一个 `FamilySessionDialogueStore`，Store 生命周期与当前连接一致，并在连接关闭时调用 `clear()`。

本阶段尚未调用 `get_dialogue()`，也未使用 Store 替换官方 `self.dialogue`；正式聊天仍只使用原 Dialogue。个人及匿名 Dialogue 的 system prompt 和 few-shot 复制留待后续阶段。

## B4a 短期 Dialogue 正式隔离

家庭功能关闭时，所有入口继续使用官方 `self.dialogue`。家庭功能开启时，`ConnectionHandler.get_dialogue_for_turn()` 根据不可变 `TurnIdentityContext` 选择当前连接内的个人 Dialogue；无上下文或任一识别失败状态统一选择该连接的匿名 Dialogue。

Store 工厂通过 `Dialogue.copy_static_context()` 深复制官方 Dialogue 中的 system 消息和 `is_temporary` 静态 few-shot，不复制用户、assistant、tool 等真实动态历史。每个新 Dialogue 的消息及嵌套工具调用数据彼此独立。

`chat()` 在每轮开始只选择一次局部 `active_dialogue`，并将其显式传给工具结果处理；意图流程继承语音轮次的同一对象。文本、唤醒问候、无语音自动提示和来电等没有可靠声纹上下文的入口使用匿名 Dialogue，不修改 `self.dialogue`进行动态切换。

本阶段仅隔离短期上下文。PowerMem 查询、画像、断开整段保存及其官方 `device_id` 行为均未修改，因此尚不能作为完整个人长期记忆功能部署。

## B4b PowerMem 个人读取路由

家庭功能关闭时，`ConnectionHandler` 继续按官方旧签名调用
`query_memory(query)`，由 Provider 使用当前 `role_id/device_id`，用户画像也继续使用
官方单值缓存路径。

家庭功能开启时，只有不可变 `TurnIdentityContext` 中通过
`MemoryAccessPolicy.user_id_for_read()` 授权、且精确匹配
`family_id:person_id` 的 `memory_user_id` 才能进入 PowerMem。
`query_memory(query, user_id=memory_user_id)`、`get_user_profile(user_id=memory_user_id)`
和底层 PowerMem SDK 的 `search/profile` 均使用本次调用的局部参数，不修改共享
Provider 的 `role_id`。

显式用户画像缓存仅存在于进程内，并按已验证的 `memory_user_id` 分键；
标准库锁只保护缓存字典的读写。匿名、身份失败、空上下文和非法已识别结果不调用
任何私人记忆或画像读取，也不创建或读取画像缓存项，不回退到设备或上一位人员。
查询异常时，本轮以无长期记忆继续生成回答。

记忆和画像只作为 `get_llm_dialogue_with_memory()` 的本轮局部输入构建 LLM
消息副本，不写入个人 Dialogue、匿名 Dialogue 或静态模板。本阶段不修改
`save_memory()`、逐轮保存或断开连接整段保存。

## B4c PowerMem 个人逐轮写入

家庭功能开启时，每个最外层 `chat(depth=0)` 建立仅属于当前逻辑轮次的局部保存状态。工具 `REQLLM` 递归显式复用同一个状态和原始不可变 `TurnIdentityContext`；递归层不单独保存，只有最外层在完整最终回答形成后尝试一次写入。

写入前继续复用 `MemoryAccessPolicy.user_id_for_write()` 和
`family_id:person_id` 精确匹配校验。只有已识别且允许写入的人员使用
`save_memory(messages, user_id=memory_user_id)`；匿名、所有失败状态、空上下文、
非法身份、空输入、空回答、中断或异常轮次均不调用私人保存，也不回退到
`device_id`、显示名称、声纹凭据或上一位人员。

每次家庭个人写入严格只包含两条 OpenAI 角色消息：当前用户输入与按实际输出顺序
聚合的最终 assistant 自然语言。system、静态 few-shot、记忆和画像注入、
tool_calls、tool 结果、其他人员历史及此前轮次均不进入保存内容。保存失败只记录
现有风格的错误，不重试、不切换身份，也不影响已经生成的回答。

PowerMem Provider 的 `save_memory()` 增加向后兼容的可选 `user_id` 参数。显式值
直接传给 SDK 的 `UserMemory.add()` 或 `AsyncMemory.add()`，共享 Provider 的
`role_id` 不参与人员切换；未传值时仍使用官方 `role_id/device_id` 行为。

家庭模式连接断开时只跳过官方整段 Dialogue 记忆保存线程，仍执行标题、连接关闭、
资源清理和 Store 清空，不补存个人或匿名历史。家庭功能关闭时，原整段 Dialogue、
`role_id/device_id`、daemon 线程和容错行为保持不变。

当前项目声明 `powermem>=0.3.1`，实际部署版本及共享客户端的并发能力仍须在部署
验收阶段确认和压测；本阶段只保证每次调用显式携带独立 `user_id`，不引入共享可变
人员状态。

## B6 人员映射管理与部署预检

官方声纹系统继续独立负责声纹注册和识别，并产生 `voiceprint_id`。项目内管理工具
只负责把已有 `voiceprint_id` 绑定到稳定的 `family_id + person_id`；不录音、不提取
声纹特征、不调用官方声纹服务，也不修改官方声纹数据库。

`display_name` 只用于显示。PowerMem 身份始终调用 `build_memory_user_id()` 生成
`family_id:person_id`，不接受清单填写的记忆 ID，也不根据亲属称谓或显示名称推断人员。

JSON 清单必须先执行只读 `plan`。`plan` 对已有 SQLite 使用只读连接，对不存在的数据
库只报告待新增项目，不创建数据库、WAL 或 SHM。`apply` 必须显式提供与清单精确一致
的 `--confirm-family-id`，并在写入前重新计划；人员更新和新增声纹绑定由 Repository
在同一个事务内完成，重复应用同一清单保持幂等。

清单中没有出现的人员不会被删除，某成员 `voiceprint_ids` 中没有出现的既有绑定也不会
被撤销。停用人员、撤销声纹和替换声纹必须使用明确命令；撤销历史保留，不能通过工具
绕过全局 `voiceprint_id` 唯一约束。

`preflight` 只检查 Python、PowerMem 包和接口签名、家庭配置、持久化路径及身份库
schema。它不会实例化 PowerMem 客户端，不调用 `search/profile/add`，也不读取或写入
私人记忆和用户画像。

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
