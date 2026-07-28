package xiaozhi.modules.familymemory.service;

import java.nio.file.InvalidPathException;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import lombok.RequiredArgsConstructor;
import xiaozhi.common.exception.RenException;
import xiaozhi.modules.config.service.ConfigService;
import xiaozhi.modules.familymemory.dto.FamilyMemoryOperationDTO;
import xiaozhi.modules.familymemory.dto.FamilyMemorySettingsDTO;
import xiaozhi.modules.sys.dto.SysParamsDTO;
import xiaozhi.modules.sys.service.SysParamsService;

/**
 * 家庭记忆配置与安全转发服务；不访问身份SQLite。
 */
@Service
@RequiredArgsConstructor
public class FamilyMemoryService {
    static final String ENABLED_CODE = "family_memory.enabled";
    static final String FAMILY_ID_CODE = "family_memory.family_id";
    static final String DATABASE_PATH_CODE = "family_memory.database_path";
    static final String DEFAULT_DATABASE_PATH = "data/family_identity.db";

    private final SysParamsService sysParamsService;
    private final ConfigService configService;
    private final FamilyMemoryServerGateway serverGateway;

    public Map<String, Object> getSettings() {
        String enabledText = sysParamsService.getValue(ENABLED_CODE, true);
        String familyId = sysParamsService.getValue(FAMILY_ID_CODE, true);
        String databasePath = sysParamsService.getValue(DATABASE_PATH_CODE, true);

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("enabled", Boolean.parseBoolean(enabledText));
        result.put("family_id", defaultString(familyId, ""));
        result.put(
                "database_path",
                defaultString(databasePath, DEFAULT_DATABASE_PATH));
        result.put(
                "configured",
                StringUtils.isNotBlank(familyId)
                        && !"null".equalsIgnoreCase(familyId));
        return result;
    }

    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> updateSettings(FamilyMemorySettingsDTO dto) {
        validateSettings(dto);
        Map<String, Object> oldSettings = getSettings();
        Map<String, Object> desired = settingsMap(dto);

        if (Boolean.TRUE.equals(dto.getEnabled())) {
            if (StringUtils.isBlank(dto.getTargetWs())) {
                throw new RenException("启用前必须选择目标服务端并完成环境检查");
            }
            Map<String, Object> preflight = serverGateway.request(
                    dto.getTargetWs(),
                    "preflight",
                    desired,
                    Map.of());
            if (!Boolean.TRUE.equals(preflight.get("ok"))) {
                throw new RenException("环境检查存在FAIL，禁止开启家庭记忆");
            }
        }

        upsert(ENABLED_CODE, dto.getEnabled().toString(), "boolean", "是否启用家庭多人个人记忆");
        upsert(FAMILY_ID_CODE, dto.getFamilyId(), "string", "家庭永久ID");
        upsert(DATABASE_PATH_CODE, dto.getDatabasePath(), "string", "家庭身份数据库路径");
        configService.getConfig(false);

        Map<String, Object> result = new LinkedHashMap<>(desired);
        result.put("restart_required", !oldSettings.entrySet().containsAll(desired.entrySet()));
        return result;
    }

    public Map<String, Object> preflight(FamilyMemorySettingsDTO dto) {
        validateSettings(dto);
        return serverGateway.request(
                requireTarget(dto.getTargetWs()),
                "preflight",
                settingsMap(dto),
                Map.of());
    }

    public Map<String, Object> listPersons(String targetWs) {
        return forward(targetWs, "person_list", Map.of());
    }

    public Map<String, Object> showPerson(String targetWs, String personId) {
        return forward(targetWs, "person_show", Map.of("person_id", requireText(personId, "person_id")));
    }

    public Map<String, Object> createPerson(FamilyMemoryOperationDTO dto) {
        return forward(
                dto.getTargetWs(),
                "person_create",
                Map.of(
                        "person_id", requireText(dto.getPersonId(), "person_id"),
                        "display_name", requireText(dto.getDisplayName(), "display_name")));
    }

    public Map<String, Object> renamePerson(
            String personId,
            FamilyMemoryOperationDTO dto) {
        return forward(
                dto.getTargetWs(),
                "person_rename",
                Map.of(
                        "person_id", requireText(personId, "person_id"),
                        "display_name", requireText(dto.getDisplayName(), "display_name")));
    }

    public Map<String, Object> setPersonEnabled(
            String personId,
            FamilyMemoryOperationDTO dto) {
        if (dto.getEnabled() == null) {
            throw new RenException("enabled不能为空");
        }
        return forward(
                dto.getTargetWs(),
                "person_set_enabled",
                Map.of(
                        "person_id", requireText(personId, "person_id"),
                        "enabled", dto.getEnabled()));
    }

    public Map<String, Object> listVoiceprints(String targetWs, String personId) {
        Map<String, Object> payload = StringUtils.isBlank(personId)
                ? Map.of()
                : Map.of("person_id", personId);
        return forward(targetWs, "voiceprint_list", payload);
    }

    public Map<String, Object> bindVoiceprint(FamilyMemoryOperationDTO dto) {
        return forward(
                dto.getTargetWs(),
                "voiceprint_bind",
                Map.of(
                        "person_id", requireText(dto.getPersonId(), "person_id"),
                        "voiceprint_id", requireText(dto.getVoiceprintId(), "voiceprint_id")));
    }

    public Map<String, Object> revokeVoiceprint(FamilyMemoryOperationDTO dto) {
        return forward(
                dto.getTargetWs(),
                "voiceprint_revoke",
                Map.of("voiceprint_id", requireText(dto.getVoiceprintId(), "voiceprint_id")));
    }

    public Map<String, Object> replaceVoiceprint(FamilyMemoryOperationDTO dto) {
        return forward(
                dto.getTargetWs(),
                "voiceprint_replace",
                Map.of(
                        "person_id", requireText(dto.getPersonId(), "person_id"),
                        "old_voiceprint_id", requireText(dto.getOldVoiceprintId(), "old_voiceprint_id"),
                        "new_voiceprint_id", requireText(dto.getNewVoiceprintId(), "new_voiceprint_id")));
    }

    private Map<String, Object> forward(
            String targetWs,
            String operation,
            Map<String, Object> payload) {
        Map<String, Object> settings = getSettings();
        requireText((String) settings.get("family_id"), "family_id");
        return serverGateway.request(
                requireTarget(targetWs),
                operation,
                Map.of(
                        "enabled", settings.get("enabled"),
                        "family_id", settings.get("family_id"),
                        "database_path", settings.get("database_path")),
                payload);
    }

    private void upsert(String code, String value, String type, String remark) {
        String existing = sysParamsService.getValue(code, false);
        if (existing != null) {
            sysParamsService.updateValueByCode(code, value);
            return;
        }
        SysParamsDTO param = new SysParamsDTO();
        param.setParamCode(code);
        param.setParamValue(value);
        param.setValueType(type);
        param.setRemark(remark);
        sysParamsService.save(param);
    }

    static void validateSettings(FamilyMemorySettingsDTO dto) {
        if (dto == null || dto.getEnabled() == null) {
            throw new RenException("enabled不能为空");
        }
        requireText(dto.getFamilyId(), "family_id");
        String databasePath = requireText(dto.getDatabasePath(), "database_path");
        if (containsControl(databasePath)) {
            throw new RenException("database_path不能包含控制字符");
        }
        String portable = databasePath.replace('\\', '/');
        if (portable.startsWith("/")
                || portable.matches("^[A-Za-z]:/.*")) {
            throw new RenException("database_path必须是项目内相对路径");
        }
        try {
            Path normalized = Path.of(portable).normalize();
            Path fileName = normalized.getFileName();
            if (normalized.startsWith("..")
                    || fileName == null
                    || !"family_identity.db".equals(fileName.toString())) {
                throw new RenException("database_path必须指向项目内的family_identity.db");
            }
        } catch (InvalidPathException exception) {
            throw new RenException("database_path格式无效", exception);
        }
    }

    private static Map<String, Object> settingsMap(FamilyMemorySettingsDTO dto) {
        Map<String, Object> settings = new LinkedHashMap<>();
        settings.put("enabled", dto.getEnabled());
        settings.put("family_id", dto.getFamilyId());
        settings.put("database_path", dto.getDatabasePath());
        return settings;
    }

    private static String requireTarget(String targetWs) {
        return requireText(targetWs, "targetWs");
    }

    private static String requireText(String value, String fieldName) {
        if (StringUtils.isBlank(value) || "null".equalsIgnoreCase(value)) {
            throw new RenException(fieldName + "不能为空");
        }
        if (containsControl(value)) {
            throw new RenException(fieldName + "不能包含控制字符");
        }
        return value;
    }

    private static boolean containsControl(String value) {
        return value.codePoints().anyMatch(Character::isISOControl);
    }

    private static String defaultString(String value, String defaultValue) {
        return StringUtils.isBlank(value) || "null".equalsIgnoreCase(value)
                ? defaultValue
                : value;
    }
}
