from .v1_to_v2 import LlmServiceType, migrate_v1_to_v2
from .v2_to_v3 import migrate_v2_to_v3

def migrate_setting(cfg: dict) -> None:
    """自动执行所有配置迁移"""
    # 依次执行各版本的迁移逻辑
    migrate_v1_to_v2(cfg)
    migrate_v2_to_v3(cfg)
    # 若后续有其他迁移逻辑，可以在这里继续追加，如：
    # migrate_v3_to_v4(cfg)
