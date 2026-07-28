package xiaozhi.modules.familymemory;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;

import org.apache.shiro.authz.annotation.RequiresPermissions;
import org.junit.jupiter.api.Test;

import xiaozhi.common.exception.RenException;
import xiaozhi.common.redis.RedisUtils;
import xiaozhi.modules.device.service.DeviceService;
import xiaozhi.modules.familymemory.controller.FamilyMemoryController;
import xiaozhi.modules.familymemory.service.FamilyMemoryServerGateway;
import xiaozhi.modules.sys.service.SysParamsService;

class FamilyMemorySecurityContractTest {
    @Test
    void everyControllerMethodInheritsSuperAdminAuthentication() {
        RequiresPermissions permissions = FamilyMemoryController.class
                .getAnnotation(RequiresPermissions.class);

        assertArrayEquals(new String[] { "sys:role:superAdmin" }, permissions.value());
        assertTrue(Arrays.stream(FamilyMemoryController.class.getDeclaredMethods())
                .map(Method::getName)
                .noneMatch(name -> name.toLowerCase().contains("delete")));
    }

    @Test
    void unauthenticatedRequestsAreCoveredByGlobalOauthFilter() throws Exception {
        String shiroConfig = Files.readString(
                Path.of(
                        "src/main/java/xiaozhi/modules/security/config/"
                                + "ShiroConfig.java"),
                StandardCharsets.UTF_8);

        assertTrue(shiroConfig.contains(
                "filterMap.put(\"/**\", \"oauth2\")"));
        assertFalse(shiroConfig.contains(
                "filterMap.put(\"/admin/family-memory"));
    }

    @Test
    void gatewayOnlyAcceptsConfiguredServerTargets() {
        SysParamsService params = mock(SysParamsService.class);
        when(params.getValue("server.websocket", true))
                .thenReturn("ws://first/xiaozhi/v1/;ws://second/xiaozhi/v1/");
        FamilyMemoryServerGateway gateway = new FamilyMemoryServerGateway(
                params,
                mock(DeviceService.class),
                mock(RedisUtils.class));

        assertThrows(
                RenException.class,
                () -> gateway.request(
                        "ws://attacker/xiaozhi/v1/",
                        "preflight",
                        java.util.Map.of(),
                        java.util.Map.of()));
    }

    @Test
    void managerApiFamilyModuleContainsNoSqliteAccessOrDeletionApi() throws Exception {
        Path moduleRoot = Path.of("src/main/java/xiaozhi/modules/familymemory");
        String source;
        try (var paths = Files.walk(moduleRoot)) {
            source = paths
                    .filter(path -> path.toString().endsWith(".java"))
                    .map(path -> {
                        try {
                            return Files.readString(path, StandardCharsets.UTF_8);
                        } catch (Exception exception) {
                            throw new RuntimeException(exception);
                        }
                    })
                    .reduce("", (left, right) -> left + "\n" + right);
        }

        assertFalse(source.contains("java.sql"));
        assertFalse(source.contains("org.sqlite"));
        assertFalse(source.contains("jdbc:sqlite"));
        assertFalse(source.contains("SQLiteIdentityRepository"));
        assertFalse(source.contains("@DeleteMapping"));
        assertFalse(source.contains("PowerMem"));
    }

    @Test
    void familyManagementConnectionUsesOneTimeInternalAdminMarker() throws Exception {
        String gateway = Files.readString(
                Path.of(
                        "src/main/java/xiaozhi/modules/familymemory/service/"
                                + "FamilyMemoryServerGateway.java"),
                StandardCharsets.UTF_8);
        String configService = Files.readString(
                Path.of(
                        "src/main/java/xiaozhi/modules/config/service/impl/"
                                + "ConfigServiceImpl.java"),
                StandardCharsets.UTF_8);

        assertTrue(gateway.contains(
                "INTERNAL_ADMIN_MARKER = \"family_memory_admin\""));
        assertTrue(configService.contains(
                "adminConfig.put(\"family_memory_admin_authorized\", true)"));
        assertTrue(configService.contains("redisUtils.delete(redisKey)"));
    }
}
