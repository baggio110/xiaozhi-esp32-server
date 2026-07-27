# 家庭个人记忆软件集成测试

## 测试边界

B7 使用完全本地的测试替身验证现有生产链路，不启动真实 WebSocket、ASR、LLM、
PowerMem、voiceprint-api、manager-api、Docker 或群晖服务，也不实现新的模拟器产品。

测试从 `ConnectionHandler` 源码加载现有路由、工具递归、逐轮保存和断开保存方法，
配合真实的 `IdentityService`、`FamilySessionDialogueStore`、`Dialogue` 与身份模型。
所有 Fake 仅位于测试目录。

## 软件双终端模型

| 终端 | device_id | 短期 Dialogue | 长期记忆身份 |
| --- | --- | --- | --- |
| 客厅 | `living_room_device` | 连接内独立 | `family_id:person_id` |
| 卧室 | `bedroom_device` | 连接内独立 | `family_id:person_id` |

同一个人在两个终端拥有不同短期 Dialogue 对象，但通过相同
`memory_user_id` 读取和写入同一份长期 PowerMem。测试明确禁止用 `device_id`
作为家庭模式私人记忆身份。

## 多人员与匿名矩阵

- 爷爷、奶奶、两个孩子、照护人员及同名不同 `person_id` 人员均使用独立短期
  Dialogue 和长期记忆身份。
- 人员再次出现时复用当前连接中自己的短期 Dialogue。
- 匿名及全部失败状态使用连接级匿名 Dialogue，私人记忆查询、画像和保存均为零。
- 同一连接的匿名连续轮次共享短期历史；新连接不继承匿名历史；断开时清空。
- `display_name`、`speaker_name` 和亲属称谓不参与 `memory_user_id`。

## 工具、并发和故障

- REQLLM 递归沿用最外层 `TurnIdentityContext`，只保存原始用户输入和最终自然语言回答。
- `tool_calls`、工具结果、system、few-shot、记忆和画像注入不进入逐轮保存内容。
- 并发测试覆盖不同终端不同人员、同一人员跨终端，以及个人和匿名 Dialogue 首次创建。
- 故障注入覆盖声纹失败、记忆读取失败、画像失败、保存失败、工具失败、LLM异常和轮次中断。
- 家庭模式断开不进行整段保存；家庭功能关闭时继续使用官方 Dialogue、`role_id/device_id`
  和断开整段保存。

## Fake 服务

- `FakeWebSocket`：只记录发送与关闭。
- `FakeLLM`：按输入和注入记忆返回确定性自然语言流。
- `FakeMemoryProvider`：按显式 `user_id` 记录查询、画像、保存和并发调用。
- `FakeVoiceprintProvider`：产生结构化 `RecognitionResult`，由真实身份服务解析。
- `FakeTool`：覆盖 RESPONSE、RECORD、REQLLM 和失败行为。

## 尚需实体环境验证

当前测试证明本项目代码不通过共享可变身份造成串线，但不代表：

- ESP32实体终端的拾音、VAD、ASR和声纹质量已经验收；
- 真实网络断开、重连和时序抖动已经覆盖；
- PowerMem SDK已经完成真实跨线程或高并发压测；
- 群晖卷映射、容器权限、资源限制和镜像升级已经验证。

这些项目必须在后续受控镜像构建和实体双终端环境中单独验收。
