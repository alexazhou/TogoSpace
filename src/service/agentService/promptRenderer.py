"""Prompt 模板渲染引擎（基于 Jinja2）。

为各业务场景提供基于独立 `.j2` 模板文件的渲染能力。
模板文件集中存放于 `src/service/agentService/templates/`。
"""

from __future__ import annotations

import os
from typing import Any

import jinja2
import jinja2.runtime
import yaml

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")


class _PromptYamlDumper(yaml.Dumper):
    """YAML Dumper：多行字符串使用 literal block 样式（|），列表项保持缩进。"""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow=flow, indentless=False)

    def _str_representer(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
        if "\n" in data:
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data)

    def _undefined_representer(dumper: yaml.Dumper, data: Any) -> yaml.ScalarNode:
        return dumper.represent_scalar("tag:yaml.org,2002:null", "")

    def _dict_representer(dumper: yaml.Dumper, data: dict) -> yaml.MappingNode:
        cleaned = {
            k: v
            for k, v in data.items()
            if v is not None and not isinstance(v, jinja2.runtime.Undefined)
        }
        return dumper.represent_dict(cleaned.items())


_PromptYamlDumper.add_representer(str, _PromptYamlDumper._str_representer)
_PromptYamlDumper.add_representer(dict, _PromptYamlDumper._dict_representer)
_PromptYamlDumper.add_representer(jinja2.runtime.Undefined, _PromptYamlDumper._undefined_representer)


def to_yaml_filter(val: Any) -> str:
    """Jinja2 过滤器：将 Python 数据结构格式化为整洁的 YAML 字符串。"""
    if val is None:
        return ""
    return yaml.dump(
        val,
        Dumper=_PromptYamlDumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()


_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(_TEMPLATE_DIR),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
)
_jinja_env.filters["to_yaml"] = to_yaml_filter


def render_prompt(__template_file: str, /, **context: Any) -> str:
    """渲染指定的 Jinja2 提示词模板。

    Args:
        __template_file: 模板文件名（如 'todo_task_turn.j2' 或 'system_prompt.j2'）
        **context: 传递给模板渲染的变量（支持 template_name 等业务字段）

    Returns:
        渲染并清理两端空白后的 Prompt 字符串
    """
    template = _jinja_env.get_template(__template_file)
    return template.render(**context).strip()
