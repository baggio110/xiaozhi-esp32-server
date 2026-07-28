package xiaozhi.modules.familymemory.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

/**
 * 家庭成员和声纹管理操作参数。
 */
@Data
public class FamilyMemoryOperationDTO {
    @NotBlank
    private String targetWs;

    private String personId;
    private String displayName;
    private Boolean enabled;
    private String voiceprintId;
    private String oldVoiceprintId;
    private String newVoiceprintId;
}
