class IdentityError(ValueError):
    """身份关系或主体解析不成立。"""


class AmbiguousIdentity(IdentityError):
    """同一主体名称或同一前身对应多个法律实体，无法自动判定。"""
