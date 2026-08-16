"""V2 -> V3 配置迁移：模型支持能力由布尔 support_vision 升级为输入类型列表 input。"""


def migrate_v2_to_v3(cfg: dict) -> None:
    """向后兼容自动迁移 (V2 -> V3)：support_vision → input

    迁移规则：
    - 已存在 `input` key 的模型保持原样（尊重显式配置）。
    - 旧 `support_vision: true` → 写入 `input=["text", "image"]`。
    - 纯文本模型（无 `support_vision`）不写 `input` key，靠 pydantic 默认 `["text"]`。
    - 一律移除 `support_vision`，`version` 升为 `v3`。
    """
    version = cfg.get("version", "v1")
    if version != "v2":
        return

    for provider in cfg.get("llm_providers", []):
        for model in provider.get("models", []):
            if "input" not in model and model.get("support_vision"):
                model["input"] = ["text", "image"]
            model.pop("support_vision", None)

    cfg["version"] = "v3"
