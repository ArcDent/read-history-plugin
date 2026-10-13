"""入参规整的纯函数：把宿主或配置给的任意值折成可比较的基本类型。

本模块是三种规整的唯一实现处——「是字符串就 strip」「是数就 float」「是路径就 Path」，
被 `access_gate`、`stream_resolver`、`summary_store`、`summary_generator` 共用，避免
同一份实现散成多份而彼此漂移。不 import maibot_sdk、不 import 第三方包、不读文件、
不碰网络，本机任意 Python 3 直接 import。
"""

from __future__ import annotations

from pathlib import Path


def clean_str(value: object) -> str:
    """把任意值规整成去空白字符串；非字符串一律按空串。"""

    return value.strip() if isinstance(value, str) else ""


def as_float(value: object) -> float | None:
    """把入参规整成 float；布尔与非数值返回 None。"""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


def as_path(value: object) -> Path | None:
    """把入参规整成 Path；空值与非法类型返回 None。"""

    if isinstance(value, Path):
        return value
    if isinstance(value, str) and value.strip():
        return Path(value)
    return None


def path_in_dir(data_dir: object, file_name: str) -> Path | None:
    """把 data_dir 规整成其中某个文件的路径；拿不到目录返回 None。"""

    directory = as_path(data_dir)
    return directory / file_name if directory is not None else None
