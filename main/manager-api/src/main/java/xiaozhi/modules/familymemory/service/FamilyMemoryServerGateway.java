package xiaozhi.modules.familymemory.service;

import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.WebSocketHttpHeaders;

import com.fasterxml.jackson.databind.ObjectMapper;

import lombok.RequiredArgsConstructor;
import xiaozhi.common.constant.Constant;
import xiaozhi.common.exception.ErrorCode;
import xiaozhi.common.exception.RenException;
import xiaozhi.common.redis.RedisKeys;
import xiaozhi.common.redis.RedisUtils;
import xiaozhi.common.utils.JsonUtils;
import xiaozhi.modules.device.service.DeviceService;
import xiaozhi.modules.sys.dto.ServerActionPayloadDTO;
import xiaozhi.modules.sys.dto.ServerActionResponseDTO;
import xiaozhi.modules.sys.enums.ServerActionEnum;
import xiaozhi.modules.sys.enums.ServerActionResponseEnum;
import xiaozhi.modules.sys.service.SysParamsService;
import xiaozhi.modules.sys.utils.WebSocketClientManager;

/**
 * 复用现有设备JWT与server.secret双重校验链路访问Python服务端。
 */
@Component
@RequiredArgsConstructor
public class FamilyMemoryServerGateway {
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    public static final String INTERNAL_ADMIN_MARKER = "family_memory_admin";

    private final SysParamsService sysParamsService;
    private final DeviceService deviceService;
    private final RedisUtils redisUtils;

    public Map<String, Object> request(
            String targetWs,
            String operation,
            Map<String, Object> settings,
            Map<String, Object> payload) {
        validateTarget(targetWs);
        String serverSecret = sysParamsService.getValue(Constant.SERVER_SECRET, true);
        if (StringUtils.isBlank(serverSecret) || "null".equalsIgnoreCase(serverSecret)) {
            throw new RenException("服务端密钥未配置");
        }

        String requestId = UUID.randomUUID().toString();
        String deviceId = UUID.randomUUID().toString();
        String clientId = UUID.randomUUID().toString();
        redisUtils.set(
                RedisKeys.getTmpRegisterMacKey(deviceId),
                INTERNAL_ADMIN_MARKER,
                300);

        WebSocketHttpHeaders headers = new WebSocketHttpHeaders();
        headers.add("device-id", deviceId);
        headers.add("client-id", clientId);
        try {
            headers.add(
                    "authorization",
                    "Bearer " + deviceService.generateWebSocketToken(clientId, deviceId));
        } catch (Exception exception) {
            throw new RenException(ErrorCode.WEB_SOCKET_CONNECT_FAILED, exception);
        }

        Map<String, Object> content = new LinkedHashMap<>();
        content.put("secret", serverSecret);
        content.put("request_id", requestId);
        content.put("operation", operation);
        content.put("settings", settings);
        content.put("payload", payload == null ? Map.of() : payload);

        try (WebSocketClientManager client = new WebSocketClientManager.Builder()
                .connectTimeout(3, TimeUnit.SECONDS)
                .maxSessionDuration(15, TimeUnit.SECONDS)
                .uri(targetWs)
                .headers(headers)
                .build()) {
            client.sendJson(ServerActionPayloadDTO.build(
                    ServerActionEnum.FAMILY_MEMORY,
                    content));
            List<String> messages = client.listener(
                    text -> matchesResponse(text, requestId));
            ServerActionResponseDTO response = parseMatchingResponse(messages, requestId);
            if (response.getStatus() != ServerActionResponseEnum.SUCCESS) {
                throw new RenException(response.getMessage());
            }
            Object data = response.getContent().get("data");
            Map<String, Object> result = JsonUtils.toStringObjectMap(data);
            if (result == null) {
                throw new RenException("服务端返回的数据格式无效");
            }
            return result;
        } catch (RenException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new RenException(ErrorCode.WEB_SOCKET_CONNECT_FAILED, exception);
        }
    }

    void validateTarget(String targetWs) {
        String configured = sysParamsService.getValue(Constant.SERVER_WEBSOCKET, true);
        if (StringUtils.isBlank(configured)) {
            throw new RenException("未配置小智服务端WebSocket地址");
        }
        boolean allowed = Arrays.stream(configured.split(";"))
                .anyMatch(candidate -> candidate.equals(targetWs));
        if (!allowed) {
            throw new RenException("目标WebSocket地址不在已配置服务端白名单中");
        }
    }

    private static boolean matchesResponse(String text, String requestId) {
        try {
            ServerActionResponseDTO response = OBJECT_MAPPER.readValue(
                    text,
                    ServerActionResponseDTO.class);
            Map<String, Object> content = response.getContent();
            return content != null
                    && "server".equals(response.getType())
                    && "family_memory".equals(content.get("action"))
                    && requestId.equals(content.get("request_id"));
        } catch (Exception ignored) {
            return false;
        }
    }

    private static ServerActionResponseDTO parseMatchingResponse(
            List<String> messages,
            String requestId) {
        for (int index = messages.size() - 1; index >= 0; index--) {
            String text = messages.get(index);
            if (!matchesResponse(text, requestId)) {
                continue;
            }
            try {
                return OBJECT_MAPPER.readValue(text, ServerActionResponseDTO.class);
            } catch (Exception exception) {
                throw new RenException("服务端返回的数据格式无效", exception);
            }
        }
        throw new RenException("未收到家庭记忆管理响应");
    }
}
