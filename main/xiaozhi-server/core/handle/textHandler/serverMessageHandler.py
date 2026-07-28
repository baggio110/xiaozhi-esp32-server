import asyncio
import json
from typing import Dict, Any

from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType
from core.family_identity import FamilyIdentityError
from core.providers.tools.device_mcp import handle_mcp_message
from tools.family_memory.common import ToolOperationError

TAG = __name__
FAMILY_MEMORY_ACTION = "family_memory"

class ServerTextMessageHandler(TextMessageHandler):
    """MCP消息处理器"""

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.SERVER

    async def handle(self, conn, msg_json: Dict[str, Any]) -> None:
        # 如果配置是从API读取的，则需要验证secret
        if not conn.read_config_from_api:
            return
        if (
            msg_json.get("action") == FAMILY_MEMORY_ACTION
            and not getattr(conn, "family_memory_admin_authorized", False)
        ):
            await conn.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "fail",
                        "message": "家庭记忆管理连接身份验证失败",
                    },
                    ensure_ascii=False,
                )
            )
            return
        # 获取post请求的secret
        post_secret = msg_json.get("content", {}).get("secret", "")
        secret = conn.config["manager-api"].get("secret", "")
        # 如果secret不匹配，则返回
        if post_secret != secret:
            await conn.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "error",
                        "message": "服务器密钥验证失败",
                    }
                )
            )
            return
        if msg_json.get("action") == FAMILY_MEMORY_ACTION:
            await self._handle_family_memory(conn, msg_json)
        # 动态更新配置
        elif msg_json["action"] == "update_config":
            try:
                # 更新WebSocketServer的配置
                if not conn.server:
                    await conn.websocket.send(
                        json.dumps(
                            {
                                "type": "server",
                                "status": "error",
                                "message": "无法获取服务器实例",
                                "content": {"action": "update_config"},
                            }
                        )
                    )
                    return

                if not await conn.server.update_config():
                    await conn.websocket.send(
                        json.dumps(
                            {
                                "type": "server",
                                "status": "error",
                                "message": "更新服务器配置失败",
                                "content": {"action": "update_config"},
                            }
                        )
                    )
                    return

                # 发送成功响应
                await conn.websocket.send(
                    json.dumps(
                        {
                            "type": "server",
                            "status": "success",
                            "message": "配置更新成功",
                            "content": {"action": "update_config"},
                        }
                    )
                )
            except Exception as e:
                conn.logger.bind(tag=TAG).error(f"更新配置失败: {str(e)}")
                await conn.websocket.send(
                    json.dumps(
                        {
                            "type": "server",
                            "status": "error",
                            "message": f"更新配置失败: {str(e)}",
                            "content": {"action": "update_config"},
                        }
                    )
                )
        # 重启服务器
        elif msg_json["action"] == "restart":
            await conn.handle_restart(msg_json)

    async def _handle_family_memory(
        self,
        conn,
        msg_json: Dict[str, Any],
    ) -> None:
        """通过已验证的内部链路执行家庭记忆管理操作。"""

        content = msg_json.get("content", {})
        request_id = content.get("request_id")
        operation = content.get("operation")
        response_content = {
            "action": FAMILY_MEMORY_ACTION,
            "request_id": request_id,
            "operation": operation,
        }
        try:
            if not conn.server or not hasattr(
                conn.server,
                "family_memory_admin",
            ):
                raise RuntimeError("家庭记忆管理服务不可用")
            result = conn.server.family_memory_admin.handle(
                operation,
                content.get("settings", {}),
                content.get("payload", {}),
            )
            response_content["data"] = result
            await conn.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "success",
                        "message": "家庭记忆管理操作成功",
                        "content": response_content,
                    },
                    ensure_ascii=False,
                )
            )
        except (ValueError, TypeError) as exc:
            response_content["error_code"] = "INVALID_REQUEST"
            await conn.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "fail",
                        "message": str(exc),
                        "content": response_content,
                    },
                    ensure_ascii=False,
                )
            )
        except (ToolOperationError, FamilyIdentityError) as exc:
            response_content["error_code"] = "OPERATION_REJECTED"
            await conn.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "fail",
                        "message": str(exc),
                        "content": response_content,
                    },
                    ensure_ascii=False,
                )
            )
        except Exception:
            response_content["error_code"] = "OPERATION_FAILED"
            await conn.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "fail",
                        "message": "家庭记忆管理操作失败",
                        "content": response_content,
                    },
                    ensure_ascii=False,
                )
            )
