package xiaozhi.modules.familymemory.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

/**
 * 家庭记忆三字段配置。
 */
@Data
public class FamilyMemorySettingsDTO {
    @NotNull
    private Boolean enabled;

    @NotBlank
    private String familyId;

    @NotBlank
    private String databasePath;

    /**
     * 仅用于启用前预检，不写入配置。
     */
    private String targetWs;
}
