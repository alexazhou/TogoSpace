import os

from exception import TogoException


def ensure_dir(path: str) -> None:
    """确保目录存在。若不存在则创建，创建失败时抛出 TogoException。"""
    if os.path.exists(path):
        return
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        raise TogoException(
            f"无法创建目录 '{path}': {e.strerror}",
            error_code="directory_create_failed",
        )


def validate_absolute_path(path: str) -> None:
    """验证路径是否为绝对路径（Unix 的 / 或 Windows 的盘符）或以 ~ 开头。
    若校验不通过则抛出 TogoException。
    """
    if not (os.path.isabs(path) or path.startswith("~")):
        raise TogoException(
            f"路径必须为绝对路径或以 ~ 开头，不支持相对路径：{path}",
            error_code="invalid_path_format",
        )
