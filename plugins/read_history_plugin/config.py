"""插件配置模型：插件开关、可读范围、最近消息与中期摘要四节。

配置面只有这四节，`config.toml` 与模型默认值逐字段一致。取值规则（白名单条目的
去空白、非空与数字串判定）不在本模块写第二份，统一由 `config_rules.py` 提供，
本模块只做声明与调用，并在配置期把非法白名单整份拒绝（不静默丢弃条目，否则运维会
以为某个群已经可读）。

`[access]` 的两个字段分工明确：`readable_streams` 是群号名单，决定「哪些群能看到本插件
工具」与「哪些群作为读取目标可读」；`private_enabled` 是私聊历史的总闸，私聊可读性只由
它决定，与名单内容无关（见 ADR-0003）。
"""

from __future__ import annotations

from typing import Any, ClassVar

from maibot_sdk import Field, PluginConfigBase

from .config_rules import normalize_stream_entries


class PluginSection(PluginConfigBase):
    """`[plugin]` 节：插件总开关与配置版本。"""

    __ui_label__: ClassVar[str] = "插件开关"
    __ui_order__: ClassVar[int] = 0

    enabled: bool = Field(
        default=False,
        description="插件总开关；关闭时宿主不激活本插件，不注册任何工具。",
        json_schema_extra={
            "hint": "默认关闭：装好后先填 access.readable_streams 再打开，避免安装即暴露读取工具。",
            "label": "启用插件",
            "order": 0,
        },
    )
    config_version: str = Field(
        default="0.1.0",
        description="配置版本号；新增字段时提升（数字点分），宿主据此把新字段写回配置文件。",
        json_schema_extra={"disabled": True, "hidden": True, "label": "配置版本", "order": 99},
    )


class AccessSection(PluginConfigBase):
    """`[access]` 节：可读群号名单与私聊开关。"""

    __ui_label__: ClassVar[str] = "可读范围"
    __ui_order__: ClassVar[int] = 1

    readable_streams: list[str] = Field(
        default_factory=list,
        description="可读群号名单：只装群号，名单内的群既暴露读取工具又作为读取目标可读；写 QQ 号不产生作用。",
        json_schema_extra={
            "hint": (
                "留空 = 没有任何可读的群，也没有任何群能看到本插件工具；"
                "条目必须是数字串（去空白后），群号在名单内时才既暴露工具又可读；"
                "写 QQ 号不产生作用：既不让私聊暴露工具，也不改变私聊可读性（只看私聊开关）。"
            ),
            "label": "可读群号名单",
            "order": 0,
        },
    )
    private_enabled: bool = Field(
        default=False,
        description="私聊历史总闸：打开时全部有历史记录的私聊都可读，与白名单内容无关；关闭时读不到任何私聊历史。",
        json_schema_extra={
            "hint": "默认关闭；打开后全部有历史记录的私聊都成为可读目标，私聊里仍不暴露工具。",
            "label": "允许读私聊历史",
            "order": 1,
        },
    )

    def __init__(self, **data: Any) -> None:
        """在 SDK 字段校验前校验白名单与私聊开关（沿用 courier 配置节的先例）。

        两件事必须在字段校验前做。一是白名单整份过 `normalize_stream_entries`，任何
        一条非法就 `ValueError`：静默丢条目会让运维以为某个群已经可读。二是
        `private_enabled` 的类型检查，pydantic 的宽松归一会把 `"true"`、`1` 收成布尔，
        那时再拒绝已经晚了一帧。通过校验的白名单已经去空白去重，存进配置的就是可直接
        用作 `allowed_session` 的投影输入与群可读性判定的输入、且与门禁投影同源的形态。

        入口先复制一份入参再改写，避免把调用方传进来的 dict 就地改掉。
        """

        data = dict(data)
        raw_streams = data.get("readable_streams")
        if isinstance(raw_streams, (list, tuple)):
            normalized, rejection = normalize_stream_entries(raw_streams)
            if rejection:
                raise ValueError(f"可读聊天流白名单非法：{rejection}")
            data["readable_streams"] = normalized
        elif raw_streams is not None:
            raise ValueError("可读聊天流 readable_streams 必须是字符串列表")

        private_enabled = data.get("private_enabled", False)
        if not isinstance(private_enabled, bool):
            raise ValueError("私聊历史开关 private_enabled 必须是布尔值（true 或 false）")

        super().__init__(**data)


class RecentSection(PluginConfigBase):
    """`[recent]` 节：最近消息的条数与机器人发言开关。"""

    __ui_label__: ClassVar[str] = "最近消息"
    __ui_order__: ClassVar[int] = 2

    default_limit: int = Field(
        default=20,
        ge=1,
        description="LLM 不传 limit 时返回的消息条数。",
        json_schema_extra={"hint": "默认 20；命令消息不计入结果。", "label": "默认条数", "order": 0},
    )
    max_limit: int = Field(
        default=50,
        ge=1,
        description="单次可返回的消息条数上限；LLM 传入更大的值按本上限钳制。",
        json_schema_extra={"hint": "默认 50；超过上限不报错，按上限返回。", "label": "条数上限", "order": 1},
    )
    include_bot_messages: bool = Field(
        default=False,
        description="是否把机器人自己的发言计入结果。",
        json_schema_extra={"hint": "默认不包含机器人发言。", "label": "包含机器人发言", "order": 2},
    )


class SummarySection(PluginConfigBase):
    """`[summary]` 节：中期摘要的回溯窗口与输入上限。"""

    __ui_label__: ClassVar[str] = "中期摘要"
    __ui_order__: ClassVar[int] = 3

    window_hours: int = Field(
        default=6,
        ge=1,
        description="中期摘要默认回溯的小时数。",
        json_schema_extra={"hint": "默认 6 小时；首次生成时按此窗口取消息。", "label": "回溯小时数", "order": 0},
    )
    max_input_messages: int = Field(
        default=200,
        ge=1,
        description="喂给 planner 概括的消息条数上限。",
        json_schema_extra={"hint": "默认 200；输入超限时只取窗口内最新的这部分消息。", "label": "输入条数上限", "order": 1},
    )


class ReadHistoryConfig(PluginConfigBase):
    """插件配置模型：四个节，无其他字段面。"""

    # SDK 基类使用 pydantic；其嵌套校验错误默认会带原输入，必须在根模型隐藏。
    model_config: ClassVar[dict[str, Any]] = {"hide_input_in_errors": True}

    plugin: PluginSection = Field(default_factory=PluginSection)
    access: AccessSection = Field(default_factory=AccessSection)
    recent: RecentSection = Field(default_factory=RecentSection)
    summary: SummarySection = Field(default_factory=SummarySection)
