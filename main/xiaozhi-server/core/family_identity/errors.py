"""家庭身份模块异常。"""


class FamilyIdentityError(Exception):
    """家庭身份模块基础异常。"""


class InvalidIdentityIdError(FamilyIdentityError, ValueError):
    """身份 ID 为空或格式无效。"""


class InvalidIdentityDecisionError(FamilyIdentityError, ValueError):
    """身份决策违反安全不变量。"""


class MemoryAccessDeniedError(FamilyIdentityError, PermissionError):
    """当前身份决策不允许访问私人记忆。"""
