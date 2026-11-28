from .v1_to_v2 import LlmServiceType, migrate_v1_to_v2
from .v2_to_v3 import migrate_v2_to_v3

def migrate_setting(cfg: dict) -> None:
    """自动执行所有配置迁移"""
    migrate_v1_to_v2(cfg)
    migrate_v2_to_v3(cfg)
    # 若后续有新的迁移逻辑，在本函数末尾继续追加调用即可。
