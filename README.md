<div align="center">

# 跨群历史读取喵

MaiBot 第三方插件：按白名单把「读取其他聊天流历史」的能力暴露给 LLM，让机器人在一个群里能引用另一个群里发生过的事，形成跨群记忆。

![license](https://img.shields.io/badge/license-MIT-blue)
![maibot](https://img.shields.io/badge/MaiBot-%3E%3D1.3.6-blue)

</div>

## Contents

- [Background](#background)
- [Install](#install)
- [Usage](#usage)
  - [Basic](#basic)
  - [Advanced](#advanced)
- [Configuration](#configuration)
- [FAQ](#faq)
- [Maintainers](#maintainers)
- [Contributing](#contributing)
- [License](#license)

## Background

机器人待在一个群里，看不到另一个群里发生过什么。本插件按白名单把「跨群读取」作为工具暴露给群里的 LLM：当前群在名单内，工具才对该群可见；作为读取目标，也只有名单内的群可读。私聊历史由单独的总闸控制，默认关闭。工具只读预生成的「中期摘要」和最近若干条原始消息：摘要是回顾，管「前段时间聊了什么」；上下文消息是当下，管「刚才在聊什么」。

## Install

要求 MaiBot 1.3.6+（插件 SDK 2.12.0+）。

```sh
# 安装命令待补：宿主插件目录放置方式 / Docker 部署步骤
```

## Usage

插件默认关闭、白名单为空：写入插件目录后不改动任何群的既有行为。在配置里填好 `readable_streams` 并把 `enabled` 打开后，群聊里的 LLM 可以调用三个工具。

### Basic

| 工具 | 作用 |
| ---- | ---- |
| `list_readable_streams` | 列出当前可读的聊天流 |
| `read_chat_summary` | 读目标聊天流的中期摘要（预生成，未生成过的会明确告知暂无摘要） |
| `read_recent_messages` | 读目标聊天流最近若干条原始消息 |

### Advanced

<details>
<summary>config.toml 全部字段</summary>

| 字段 | 类型 | 默认值 | 说明 |
| ---- | ---- | ------ | ---- |
| `enabled` | bool | `false` | 插件总开关，默认关闭 |
| `config_version` | string | `"0.1.0"` | 宿主要求的配置版本号，新增字段时提升它 |
| `readable_streams` | list | `[]` | 可读群号名单，只装群号；写 QQ 号不产生任何作用 |
| `private_enabled` | bool | `false` | 私聊历史总闸，打开时全部有历史记录的私聊都可读 |
| `default_limit` | int | `20` | LLM 不传 `limit` 时的默认条数 |
| `max_limit` | int | `50` | 任何调用都不得超过的上限 |
| `include_bot_messages` | bool | `false` | 是否把机器人自己的发言计入结果 |
| `window_hours` | int | `6` | 中期摘要默认回溯的小时数 |
| `max_input_messages` | int | `200` | 喂给 planner 的消息条数上限 |

</details>

## Configuration

全部配置在 `plugins/read_history_plugin/config.toml`，与 `config.py` 的模型默认值逐字段一致。白名单或私聊开关变更后插件自动重载，约秒级窗口内暴露面恢复正确。

## FAQ

**Q: 私聊里能看到这几个工具吗？**
A: 不能。工具只在群聊暴露，私聊只作为被读取目标存在。

**Q: 往白名单里写 QQ 号有用吗？**
A: 没有任何作用。名单只装群号，私聊可读性只由 `private_enabled` 决定。

**Q: 为什么 `read_chat_summary` 说暂无摘要？**
A: 摘要是插件预先对目标聊天流生成的，没有生成过的聊天流不会现场补算，工具只读存储里的结果。

**Q: 改了白名单要重启吗？**
A: 不用，插件会自重载，秒级窗口内暴露面恢复正确。

## Maintainers

[@arcdent](https://github.com/arcdent)

## Contributing

PR 欢迎。仓库暂无 CONTRIBUTING.md，直接提 issue 或 PR 即可。

## License

MIT © arcdent
