"""家庭身份模块异常。"""


class FamilyIdentityError(Exception):
    """家庭身份模块基础异常。"""


class InvalidIdentityIdError(FamilyIdentityError, ValueError):
    """身份 ID 为空或格式无效。"""


class InvalidIdentityDecisionError(FamilyIdentityError, ValueError):
    """身份决策违反安全不变量。"""


class MemoryAccessDeniedError(FamilyIdentityError, PermissionError):
    """当前身份决策不允许访问私人记忆。"""


class IdentityRepositoryError(FamilyIdentityError):
    """身份仓库操作失败。"""


class UnsupportedSchemaVersionError(IdentityRepositoryError):
    """数据库结构版本高于当前代码支持范围。"""


class PersonNotFoundError(IdentityRepositoryError, LookupError):
    """指定家庭成员不存在。"""


class VoiceprintBindingError(IdentityRepositoryError):
    """声纹凭据绑定关系无效。"""


class VoiceprintAlreadyBoundError(VoiceprintBindingError):
    """声纹凭据已绑定，不能指向其他人员。"""


class VoiceprintNotFoundError(VoiceprintBindingError, LookupError):
    """指定声纹凭据不存在或不属于当前家庭。"""


class VoiceprintRevokedError(VoiceprintBindingError):
    """已撤销的声纹凭据不能恢复或重新绑定。"""
