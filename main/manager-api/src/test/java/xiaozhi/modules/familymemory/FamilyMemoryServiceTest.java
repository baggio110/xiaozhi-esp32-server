package xiaozhi.modules.familymemory;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.Map;

import org.junit.jupiter.api.Test;

import xiaozhi.common.exception.RenException;
import xiaozhi.modules.config.service.ConfigService;
import xiaozhi.modules.familymemory.dto.FamilyMemoryOperationDTO;
import xiaozhi.modules.familymemory.dto.FamilyMemorySettingsDTO;
import xiaozhi.modules.familymemory.service.FamilyMemoryServerGateway;
import xiaozhi.modules.familymemory.service.FamilyMemoryService;
import xiaozhi.modules.sys.dto.SysParamsDTO;
import xiaozhi.modules.sys.service.SysParamsService;

class FamilyMemoryServiceTest {
    private final SysParamsService sysParamsService = mock(SysParamsService.class);
    private final ConfigService configService = mock(ConfigService.class);
    private final FamilyMemoryServerGateway gateway = mock(FamilyMemoryServerGateway.class);
    private final FamilyMemoryService service = new FamilyMemoryService(
            sysParamsService,
            configService,
            gateway);

    @Test
    void missingParametersDefaultToDisabledWithoutCreatingRows() {
        Map<String, Object> settings = service.getSettings();

        assertFalse((Boolean) settings.get("enabled"));
        assertEquals("", settings.get("family_id"));
        assertEquals("data/family_identity.db", settings.get("database_path"));
        assertFalse((Boolean) settings.get("configured"));
        verify(sysParamsService, never()).save(any());
    }

    @Test
    void disabledSettingsUseOnlyExistingSysParamsMechanism() {
        FamilyMemorySettingsDTO dto = settings(false);

        Map<String, Object> result = service.updateSettings(dto);

        assertFalse((Boolean) result.get("enabled"));
        assertTrue((Boolean) result.get("restart_required"));
        verify(sysParamsService, times(3)).save(any(SysParamsDTO.class));
        verify(configService).getConfig(false);
        verify(gateway, never()).request(anyString(), anyString(), any(), any());
    }

    @Test
    void enablingIsRejectedBeforePersistenceWhenPreflightFails() {
        FamilyMemorySettingsDTO dto = settings(true);
        dto.setTargetWs("ws://server/xiaozhi/v1/");
        when(gateway.request(
                eq(dto.getTargetWs()),
                eq("preflight"),
                any(),
                eq(Map.of()))).thenReturn(Map.of("ok", false, "overall", "FAIL"));

        assertThrows(RenException.class, () -> service.updateSettings(dto));

        verify(sysParamsService, never()).save(any());
        verify(sysParamsService, never()).updateValueByCode(anyString(), anyString());
        verify(configService, never()).getConfig(anyBoolean());
    }

    @Test
    void recognizedPreflightAllowsSavingAllThreeParameters() {
        FamilyMemorySettingsDTO dto = settings(true);
        dto.setTargetWs("ws://server/xiaozhi/v1/");
        when(gateway.request(
                eq(dto.getTargetWs()),
                eq("preflight"),
                any(),
                eq(Map.of()))).thenReturn(Map.of("ok", true, "overall", "PASS"));

        Map<String, Object> result = service.updateSettings(dto);

        assertTrue((Boolean) result.get("enabled"));
        assertEquals("family_a", result.get("family_id"));
        verify(gateway).request(
                eq(dto.getTargetWs()),
                eq("preflight"),
                eq(Map.of(
                        "enabled", true,
                        "family_id", "family_a",
                        "database_path", "data/family_identity.db")),
                eq(Map.of()));
        verify(sysParamsService, times(3)).save(any(SysParamsDTO.class));
    }

    @Test
    void personAndVoiceprintOperationsNeverAcceptFamilyIdFromBrowser() {
        configuredSettings();
        FamilyMemoryOperationDTO operation = new FamilyMemoryOperationDTO();
        operation.setTargetWs("ws://server/xiaozhi/v1/");
        operation.setPersonId("person_a");
        operation.setDisplayName("中文 名称");
        operation.setVoiceprintId("voice_a");
        when(gateway.request(anyString(), anyString(), any(), any()))
                .thenReturn(Map.of("ok", true));

        service.createPerson(operation);
        service.bindVoiceprint(operation);

        verify(gateway).request(
                eq(operation.getTargetWs()),
                eq("person_create"),
                eq(Map.of(
                        "enabled", false,
                        "family_id", "family_a",
                        "database_path", "data/family_identity.db")),
                eq(Map.of(
                        "person_id", "person_a",
                        "display_name", "中文 名称")));
        verify(gateway).request(
                eq(operation.getTargetWs()),
                eq("voiceprint_bind"),
                eq(Map.of(
                        "enabled", false,
                        "family_id", "family_a",
                        "database_path", "data/family_identity.db")),
                eq(Map.of(
                        "person_id", "person_a",
                        "voiceprint_id", "voice_a")));
    }

    @Test
    void absoluteAndTraversalDatabasePathsAreRejected() {
        FamilyMemorySettingsDTO absolute = settings(false);
        absolute.setDatabasePath("C:\\private\\family_identity.db");
        FamilyMemorySettingsDTO traversal = settings(false);
        traversal.setDatabasePath("../data/family_identity.db");

        assertThrows(RenException.class, () -> service.updateSettings(absolute));
        assertThrows(RenException.class, () -> service.updateSettings(traversal));
        verify(sysParamsService, never()).save(any());
    }

    private FamilyMemorySettingsDTO settings(boolean enabled) {
        FamilyMemorySettingsDTO dto = new FamilyMemorySettingsDTO();
        dto.setEnabled(enabled);
        dto.setFamilyId("family_a");
        dto.setDatabasePath("data/family_identity.db");
        return dto;
    }

    private void configuredSettings() {
        when(sysParamsService.getValue("family_memory.enabled", true)).thenReturn("false");
        when(sysParamsService.getValue("family_memory.family_id", true)).thenReturn("family_a");
        when(sysParamsService.getValue("family_memory.database_path", true))
                .thenReturn("data/family_identity.db");
    }
}
