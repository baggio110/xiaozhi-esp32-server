# 家庭身份与个人记忆设计

## 范围

阶段 A 只建立数据模型、接口契约、失败关闭策略和自动化测试，不接入现有业务调用链。

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
