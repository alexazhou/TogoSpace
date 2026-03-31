import logging
import json

from enum import Enum, auto
import datetime as dt
from decimal import Decimal
import inspect
from inspect import FullArgSpec
from typing import TypeVar, Generic, Dict, List, Union, Optional, Any, Type, cast


logger = logging.getLogger(__name__)

T = TypeVar('T')


class JSONConfig(Enum):
    sort_key = auto()
    indent = auto()
    ensure_ascii = auto()
    datetime_format = auto()  # datetime 格式
    date_format = auto()
    time_format = auto()
    enum_use_name = auto()  # enum 类型 dump 为 name 或者 value
    ignore_unknown_key = auto()


default_json_config: Dict[JSONConfig, Any] = {
    JSONConfig.sort_key: True,
    JSONConfig.indent: 4,
    JSONConfig.ensure_ascii: False,
    JSONConfig.datetime_format: "%Y-%m-%d %H:%M:%S.%f",
    JSONConfig.date_format: "%Y-%m-%d",
    JSONConfig.time_format: "%H:%M:%S.%f",
    JSONConfig.enum_use_name: True,
    JSONConfig.ignore_unknown_key: True  # json 转对象时忽略未知 key
}


def get_format_from_type(cls: type, config: dict) -> str:
    date_format = None
    if issubclass(cls, dt.datetime):
        date_format = config.get(JSONConfig.datetime_format)
    elif issubclass(cls, dt.date):
        date_format = config.get(JSONConfig.date_format)
    elif issubclass(cls, dt.time):
        date_format = config.get(JSONConfig.time_format)

    return date_format


def json_dump(obj: object, config: Dict = None) -> str:

    final_config = default_json_config.copy()
    if config is not None:
        final_config.update(config)

    def convert_to_builtin_type(obj):
        if hasattr(obj, 'to_json'):
            return obj.to_json()
        if isinstance(obj, set):
            return list(obj)
        elif isinstance(obj, (dt.datetime, dt.time, dt.date)):
            date_format = get_format_from_type(type(obj), final_config)
            return obj.strftime(date_format)
        elif isinstance(obj, Decimal):
            return float(obj)
        elif isinstance(obj, Enum):
            return obj.name
        elif hasattr(obj, '__slots__'):
            ret = {}
            for name in obj.__slots__:
                ret[name] = getattr(obj, name)
            return ret
        elif hasattr(obj, '__dict__'):
            ret = {}
            ret.update(obj.__dict__)
            return ret
        else:
            return f"unknown type:{type(obj)}, value:{obj}"

    return json.dumps(obj,
                      sort_keys=final_config[JSONConfig.sort_key],
                      indent=final_config[JSONConfig.indent],
                      ensure_ascii=final_config[JSONConfig.ensure_ascii],
                      default=convert_to_builtin_type)


def json_load(data_str: str, cls: Type[T] = Dict, config: Dict = None) -> T:
    if data_str is None:
        return None

    data = json.loads(data_str)
    return json_data_to_object(data, cls, config)


def json_data_to_object(data: Union[Dict, List, str], cls: Type[T] = Dict, config: Dict = None) -> T:

    final_config = default_json_config.copy()
    if config is not None:
        final_config.update(config)

    def json_to_model(data: Union[Dict, List, str], cls_annotation: Type):

        if data is None:
            return None

        cls = annotation_to_type(cls_annotation)
        if issubclass(cls, (str, int, float)):
            return data
        elif issubclass(cls, (List)):
            if data is None or cls_annotation == List:
                return data
            else:
                nest_type_annotation = cls_annotation.__args__[0]
                ret_list = []
                for nest_item_data in data:
                    ret_list.append(json_to_model(nest_item_data, nest_type_annotation))
                return ret_list
        elif issubclass(cls, (Dict)):
            if data is None or cls_annotation == Dict:
                return data
            else:
                nest_type_annotation_k = cls_annotation.__args__[0]
                nest_type_annotation_v = cls_annotation.__args__[1]

                ret_dict = {}
                assert type(data) == dict
                for nest_item_data_k in data.keys():
                    nest_item_data_v = data[nest_item_data_k]
                    ret_dict[json_to_model(nest_item_data_k, nest_type_annotation_k)] = json_to_model(nest_item_data_v, nest_type_annotation_v)

                return ret_dict

        elif issubclass(cls, Enum):
            assert type(data) == str
            return getattr(cls, data)

        elif issubclass(cls, Decimal):
            return Decimal(str(data))

        elif issubclass(cls, (dt.datetime, dt.date, dt.time)):
            assert type(data) == str

            date_format = get_format_from_type(cls, final_config)
            datetime = dt.datetime.strptime(data, date_format)

            if issubclass(cls, dt.datetime):
                return datetime

            if issubclass(cls, dt.date):
                return datetime.date()
            elif issubclass(cls, dt.time):
                return datetime.time()
        else:
            assert type(data) == dict

            args: FullArgSpec = inspect.getfullargspec(cls.__init__)

            args_count = len(args.args)
            default_args_count = 0
            if args.defaults is not None:
                default_args_count = len(args.defaults)

            init_args_count = args_count - (default_args_count + 1)
            init_args = [None] * init_args_count

            empty_item = cls(*init_args)

            args_annotations = get_cls_args_annotations(cls, {})

            for name in data.keys():
                attr_json_value = data.get(name)
                attr_cls = args_annotations.get(name)
                if attr_cls is None and final_config[JSONConfig.ignore_unknown_key] is False:
                    raise Exception(f"unknown data key:{name}")

                if attr_cls is not None:
                    attr_value = json_to_model(attr_json_value, attr_cls)
                    empty_item.__setattr__(name, attr_value)

            return empty_item

    return json_to_model(data, cls)


def get_cls_args_annotations(cls, args):
    """获取类中带有的注解的参数（同时会递归获取父类的）"""
    if hasattr(cls, '__annotations__') is False:
        return args

    for k, v in cls.__annotations__.items():
        if k not in args.keys():
            args[k] = v

    for base in cls.__bases__:
        get_cls_args_annotations(base, args)

    return args


def object_to_json_data(obj: object, config: Dict = None):
    return json_load(json_dump(obj, config))


def annotation_to_type(annotation_type) -> type:
    """类型注解转换成类型"""
    if hasattr(annotation_type, '__origin__'):
        annotation_type = annotation_type.__origin__

    if annotation_type == Dict:
        return dict
    elif annotation_type == List:
        return list

    return annotation_type