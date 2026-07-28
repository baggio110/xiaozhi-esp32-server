package xiaozhi.modules.familymemory.controller;

import java.util.Map;

import org.apache.shiro.authz.annotation.RequiresPermissions;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import xiaozhi.common.utils.Result;
import xiaozhi.modules.familymemory.dto.FamilyMemoryOperationDTO;
import xiaozhi.modules.familymemory.dto.FamilyMemorySettingsDTO;
import xiaozhi.modules.familymemory.service.FamilyMemoryService;

/**
 * 智控台家庭记忆管理接口。
 */
@Tag(name = "家庭记忆管理")
@RestController
@RequestMapping("/admin/family-memory")
@RequiresPermissions("sys:role:superAdmin")
@RequiredArgsConstructor
public class FamilyMemoryController {
    private final FamilyMemoryService familyMemoryService;

    @GetMapping("/settings")
    @Operation(summary = "获取家庭记忆配置")
    public Result<Map<String, Object>> settings() {
        return new Result<Map<String, Object>>().ok(familyMemoryService.getSettings());
    }

    @PutMapping("/settings")
    @Operation(summary = "保存家庭记忆配置")
    public Result<Map<String, Object>> updateSettings(
            @RequestBody @Valid FamilyMemorySettingsDTO dto) {
        return new Result<Map<String, Object>>().ok(familyMemoryService.updateSettings(dto));
    }

    @PostMapping("/preflight")
    @Operation(summary = "执行家庭记忆环境检查")
    public Result<Map<String, Object>> preflight(
            @RequestBody @Valid FamilyMemorySettingsDTO dto) {
        return new Result<Map<String, Object>>().ok(
                familyMemoryService.preflight(dto));
    }

    @GetMapping("/persons")
    @Operation(summary = "列出家庭成员")
    public Result<Map<String, Object>> persons(@RequestParam String targetWs) {
        return new Result<Map<String, Object>>().ok(
                familyMemoryService.listPersons(targetWs));
    }

    @GetMapping("/persons/{personId}")
    @Operation(summary = "查看家庭成员详情")
    public Result<Map<String, Object>> person(
            @RequestParam String targetWs,
            @PathVariable String personId) {
        return new Result<Map<String, Object>>().ok(
                familyMemoryService.showPerson(targetWs, personId));
    }

    @PostMapping("/persons")
    @Operation(summary = "新增家庭成员")
    public Result<Map<String, Object>> createPerson(
            @RequestBody @Valid FamilyMemoryOperationDTO dto) {
        return new Result<Map<String, Object>>().ok(
                familyMemoryService.createPerson(dto));
    }

    @PutMapping("/persons/{personId}/name")
    @Operation(summary = "修改家庭成员显示名称")
    public Result<Map<String, Object>> renamePerson(
            @PathVariable String personId,
            @RequestBody @Valid FamilyMemoryOperationDTO dto) {
        return new Result<Map<String, Object>>().ok(
                familyMemoryService.renamePerson(personId, dto));
    }

    @PutMapping("/persons/{personId}/enabled")
    @Operation(summary = "启用或停用家庭成员")
    public Result<Map<String, Object>> setPersonEnabled(
            @PathVariable String personId,
            @RequestBody @Valid FamilyMemoryOperationDTO dto) {
        return new Result<Map<String, Object>>().ok(
                familyMemoryService.setPersonEnabled(personId, dto));
    }

    @GetMapping("/voiceprints")
    @Operation(summary = "列出家庭声纹绑定")
    public Result<Map<String, Object>> voiceprints(
            @RequestParam String targetWs,
            @RequestParam(required = false) String personId) {
        return new Result<Map<String, Object>>().ok(
                familyMemoryService.listVoiceprints(targetWs, personId));
    }

    @PostMapping("/voiceprints/bind")
    @Operation(summary = "绑定已有官方voiceprint_id")
    public Result<Map<String, Object>> bindVoiceprint(
            @RequestBody @Valid FamilyMemoryOperationDTO dto) {
        return new Result<Map<String, Object>>().ok(
                familyMemoryService.bindVoiceprint(dto));
    }

    @PostMapping("/voiceprints/revoke")
    @Operation(summary = "撤销声纹绑定")
    public Result<Map<String, Object>> revokeVoiceprint(
            @RequestBody @Valid FamilyMemoryOperationDTO dto) {
        return new Result<Map<String, Object>>().ok(
                familyMemoryService.revokeVoiceprint(dto));
    }

    @PostMapping("/voiceprints/replace")
    @Operation(summary = "原子替换声纹绑定")
    public Result<Map<String, Object>> replaceVoiceprint(
            @RequestBody @Valid FamilyMemoryOperationDTO dto) {
        return new Result<Map<String, Object>>().ok(
                familyMemoryService.replaceVoiceprint(dto));
    }
}
