<!-- markdownlint-disable -->

<p align="center">
  <pre>
██████   ███████  █████   ██████  
██   ██  ██      ██   ██  ██   ██ 
██████   █████   ███████  ██   ██ 
██  ██   ██      ██   ██  ██   ██ 
██   ██  ███████ ██   ██  ██████  
        跨群历史读取喵
  </pre>
</p>

<div align="center">

![MaiBot](https://img.shields.io/badge/MaiBot-1.3.6%2B-blue?style=for-the-badge)
![SDK](https://img.shields.io/badge/SDK-2.12.0%2B-00ADD8?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)
![Last Commit](https://img.shields.io/github/last-commit/arcdent/read-history-plugin?style=for-the-badge)
![Stars](https://img.shields.io/github/stars/arcdent/read-history-plugin?style=for-the-badge&logo=github)
![Issues](https://img.shields.io/github/issues/arcdent/read-history-plugin?style=for-the-badge&logo=github)

**✨ 让机器人在一个群里，讲得出另一个群发生过什么 ✨**

MaiBot 第三方插件：按白名单把「读取其他聊天流历史」的能力交给 LLM

[功能介绍](#功能介绍) • [快速开始](#快速开始) • [工具说明](#工具说明) • [权限与隐私](#权限与隐私) • [中期摘要](#中期摘要) • [高级配置](#高级配置) • [常见问题](#常见问题) • [开发与测试](#开发与测试)

</div>

---

## 🎯 项目简介

本插件是给 **MaiBot**（麦麦）用的第三方插件。它在宿主原生的工具体系里加三个 LLM 工具，让机器人在白名单群里能够读取**另一个聊天流**的历史：既可以是最近若干条原文，也可以是一段中期摘要。

> 💡 **MaiBot** 是一个基于 AI 的 QQ 机器人框架，插件通过 SDK 的组件声明向 LLM 暴露工具。
> 💡 **跨群读取**指在当前聊天流里读另一个聊天流的历史，这是本插件存在的全部理由。

### 为什么需要它？

机器人记不住隔壁群发生过的事：A 群里的人问「B 群那边昨天定了什么」，机器人只能重新问一遍。装上前因后果在别的群里时，它就需要一只手去翻那边的记录。

### 两个工具的分工

| 工具 | 回答的问题 | 内容形态 |
|:-----|:-----------|:---------|
| `read_chat_summary` | 前段时间那边聊了什么 | 由 planner 模型概括的中期摘要，是回顾 |
| `read_recent_messages` | 刚才那边在聊什么 | 最近 n 条原始消息，是当下 |

---

## ✨ 功能特点

### 🚀 核心功能

| 功能 | 描述 |
|:-----|:-----|
| **跨群读取** | 在白名单群 A 里，读取白名单群 B 的最近消息或中期摘要 |
| **目标清单** | `list_readable_streams` 先告诉 LLM 有哪些合法目标，避免它去猜群号 |
| **中期摘要** | 按需调 planner 模型概括目标聊天流并归档，读取时优先返回未过期的摘要 |
| **原文读取** | 按时间正序返回最近 n 条非命令消息，含发送者与内容 |
| **私聊目标** | 私聊开关打开时，任意有历史记录的私聊都可作为被读取目标 |
| **热更新** | 白名单或私聊开关保存后，插件自动重载，暴露面在秒级窗口内恢复正确 |

### 🔐 权限模型

| 机制 | 描述 |
|:-----|:-----|
| **白名单双重作用** | 群号在名单内，该群才既能被读、又能看到本插件工具；名单留空则谁都用不了 |
| **私聊只作目标** | 私聊里永远看不到本插件的工具，私聊只作为被读取目标存在 |
| **白名单只装群号** | 写 QQ 号不产生任何作用，既不改变私聊可读性，也不让私聊暴露工具 |
| **未确认条目不生效** | 白名单里宿主查不到对应群会话的号不会作为可读目标出现，结果里另有一行提示数量 |
| **默认关闭** | 插件开关默认关闭、白名单默认留空，装好不改变任何群的既有行为 |
| **失败不翻转** | 重载失败只记日志，已暴露工具的群继续按原状态工作，不会全体暴露或全体隐藏 |

### 🔧 工程特性

| 特性 | 描述 |
|:-----|:-----|
| **分层清晰** | 纯逻辑模块不 import `maibot_sdk`，可在任意 Python 上单测；宿主能力调用集中在胶水层 |
| **配置强校验** | 白名单条目必须是字符串且是 ASCII 十进制数字串，任何一条非法就整份拒绝，不静默丢弃 |
| **自建摘要库** | 摘要存在插件自己的 sqlite 里，与宿主数据库完全隔离 |
| **无第三方依赖** | 运行时只用宿主注入的 SDK 能力，不需要额外 pip 安装 |
| **测试覆盖** | 279 个用例，覆盖工具全链路、门禁判定、配置校验、摘要存储与渲染格式 |

---

## 🚀 快速开始

### 环境要求

| 组件 | 版本要求 | 说明 |
|:-----|:---------|:-----|
| MaiBot Host | ≥ 1.3.6 | 需要新插件体系与按会话过滤工具的 `allowed_session` 机制 |
| maibot-plugin-sdk | ≥ 2.12.0 | 与 `_manifest.json` 的 `sdk` 声明一致 |
| Python | ≥ 3.11 | 宿主容器自带，插件无需单独安装 |
| 额外依赖 | 无 | 运行时不引入第三方包 |

### 安装插件

把仓库里的 `plugins/read_history_plugin` 整个目录放进 MaiBot 的 `plugins/` 下即可，不需要打包：

```bash
git clone https://github.com/arcdent/read-history-plugin.git /tmp/read-history-plugin

# 目标路径按你实际的 MaiBot 目录调整
cp -r /tmp/read-history-plugin/plugins/read_history_plugin /path/to/MaiBot/plugins/
```

目录名可以改（宿主按插件目录里的 `_manifest.json` 读插件 ID），但目录内容要完整，`plugin.py`、`_manifest.json`、`config.toml` 三者缺一不可。

### 开启插件

插件默认关闭。在 MaiBot 的 WebUI 插件配置里打开开关、填好白名单，或直接编辑插件目录下的 `config.toml`：

```toml
[plugin]
enabled = true
config_version = "0.1.0"

[access]
# 只装群号；这些群既能看到工具，也能作为读取目标
readable_streams = ["1919810", "805457685"]
# 私聊历史总闸：默认关闭
private_enabled = false
```

保存后插件会自重载一次，白名单里的群在秒级窗口内看到工具。**首次安装时白名单可能为空，需要保存一次配置触发重载，新群才会拿到工具。**

### 验证

在白名单群里对机器人说一句话，让它去读另一个白名单群，例如：

```
看看 805457685 群最近在聊什么
```

观察机器人是否调用 `list_readable_streams` 与 `read_recent_messages`；插件内部兜底异常会写进插件日志，便于定位。

---

## 🧰 工具说明

三个工具同生共死：一个群里要么都可见，要么都不可见。

| 工具名 | 参数 | 返回 |
|:-------|:-----|:-----|
| `list_readable_streams` | 无 | 当前可读目标的类型、群号或 QQ 号与名称 |
| `read_chat_summary` | `target_group_id` 或 `target_user_id` | 目标的中期摘要，含目标标识与覆盖时间范围 |
| `read_recent_messages` | `target_group_id` 或 `target_user_id`，可选 `limit` | 最近 n 条消息，按时间正序，含发送者与内容 |

两个读取工具的目标参数互斥且必填其一，都是字符串（避免长 ID 精度损失）。错误反馈都写明正确用法并建议先调 `list_readable_streams`，方便 LLM 自我纠正。

### 可读目标清单

```
当前可读取的聊天流：
- 群聊 1919810（测试群）
- 群聊 805457685（另一个群）
- 私聊 10001（小明）
```

白名单为空且私聊开关关闭时返回：

```
当前没有可读取的聊天流。
```

白名单里有号没被确认为群时，清单末尾另加一行：

```
白名单中有 1 个条目未被确认为群，不会生效。
```

### 中期摘要

```
目标「测试群」的中期摘要（覆盖至 2026-10-13 09:00:00，生成于 2026-10-13 09:00:12）：
大家昨晚在讨论周末去哪吃，最后定在周六中午的烤肉店，A 负责订位；B 说作业想在周五前写完。
```

### 最近消息

```
目标「测试群」的最近 3 条消息（按时间正序）：
[2026-10-13 08:59:01] 群友：周六几点集合
[2026-10-13 08:59:20] 小明：十一点吧
[2026-10-13 09:00:03] 群友：好，我订位
```

### 条数与内容规则

| 规则 | 行为 |
|:-----|:-----|
| `limit` 不传 | 用配置的 `default_limit` |
| `limit` 超过上限 | 按 `max_limit` 钳制，不报错 |
| 命令消息 | 恒定过滤，不计入结果 |
| 机器人自己的发言 | 由 `include_bot_messages` 决定，默认不包含 |
| 非文本消息 | 正文显示占位符 `[非文本消息]`，不整条丢弃 |
| 目标没有消息 | 返回「目标「X」没有可查的上下文消息。」 |

### 错误反馈

| 场景 | 反馈要点 |
|:-----|:---------|
| 两个目标参数同缺或同在 | 说明二选一，并建议先调 `list_readable_streams` |
| 目标参数不是非空数字字符串 | 提示期望群号或 QQ 号 |
| 格式合法但宿主没有该会话 | 告知未找到该聊天流 |
| 群不在白名单 | 告知目标不可读并说明原因，建议让管理员加白名单 |
| 私聊开关关闭 | 告知私聊历史当前不可读 |
| 工具内部异常 | 转成明确失败文案，不向外抛出 |

---

## 🔐 权限与隐私

### 白名单是双重条件

| 位置 | 白名单的作用 |
|:-----|:-------------|
| 当前群 | 群号在名单内，该群的 LLM 才能看到三个工具 |
| 读取目标 | 只有名单内的群可读；读自己所在群的历史同样要求本群在名单内 |
| 私聊 | 不受白名单影响：私聊里不暴露工具，私聊可读性只看私聊开关 |

名单条目在配置期统一去空白、去重，并逐条校验是否为 ASCII 十进制数字串，任何一条非法都会整份拒绝并给出位置，避免运维以为某个群已经可读。

### 私聊的处理

- 私聊里看不到本插件的任何工具，因为按会话过滤对私聊会话只能靠内部哈希命中，会带来重载窗口。
- 私聊开关打开时，**全部有历史记录的私聊**都成为可读目标，与白名单内容无关；这是隐私边界被一次性放大的开关，默认为关闭。
- 目标清单里的私聊条目按最近活跃排序，最多列 20 个，超出时明确说明截断。

> ⚠️ 打开私聊开关前请确认你接受「白名单群里的 LLM 可以读到任意私聊历史」这一后果。要读任何历史都必须在白名单群里发问，私聊里问不会触发工具。

### 变更如何生效

| 变更 | 生效方式 |
|:-----|:---------|
| 白名单或私聊开关保存 | 工具内的可读性校验立即按新配置执行，不需要重载 |
| 工具在哪些群可见 | 插件在配置变更时请求宿主自重载，重建暴露面，允许秒级窗口 |
| 重载失败 | 只记日志，已注册组件与暴露面保持重载前的状态 |

---

## 🧠 中期摘要

摘要由插件自己生成并归档，宿主没有可被插件读取的持久摘要表，所以这部分数据完全属于插件。

| 环节 | 行为 |
|:-----|:-----|
| 触发 | 读取时按需生成：没有新鲜摘要才调模型，不会定时预生成 |
| 新鲜度 | 生成时刻落在 `window_hours` 窗口内直接返回，不重复生成 |
| 输入范围 | 目标已有摘要时取上次截止时间之后的增量，首次取最近 `window_hours` 小时 |
| 条数上限 | 喂给模型的消息最多 `max_input_messages` 条 |
| 模型 | 统一使用宿主的 planner 模型任务，随宿主模型配置走 |
| 失败处理 | 模型失败、范围内没消息、存储写不进去都不落盘，旧摘要原样保留，并按「从未有摘要」或「旧摘要已过期」给出对应文案 |
| 存储位置 | 插件 data_dir 下的 `summaries.sqlite3`，与宿主数据库隔离 |

表结构一张表五个字段：

```sql
CREATE TABLE IF NOT EXISTS summaries (
  target_key TEXT PRIMARY KEY,  -- 目标主键，形如 group:1919810 / private:10001
  target_type TEXT NOT NULL,    -- group 或 private
  cutoff_ts REAL NOT NULL,      -- 这条摘要覆盖到的时刻，下次概括从此处取增量
  text TEXT NOT NULL,           -- 概括正文
  created_ts REAL NOT NULL      -- 生成时刻，新鲜度判定只看它
);
```

同一目标重复生成以后写为准（`INSERT OR REPLACE`），不会出现两行。摘要质量与调用成本直接受宿主的 planner 模型配置影响，存储体积随被读取的目标数增长，清理归运维自己管。

---

## ⚙️ 高级配置

### 完整配置项

```toml
[plugin]
enabled = false
config_version = "0.1.0"

[access]
readable_streams = []
private_enabled = false

[recent]
default_limit = 20
max_limit = 50
include_bot_messages = false

[summary]
window_hours = 6
max_input_messages = 200
```

### 字段说明

| 字段 | 类型 | 默认值 | 说明 |
|:-----|:-----|:-------|:-----|
| `plugin.enabled` | bool | `false` | 插件总开关；关闭时宿主不激活插件，不注册任何工具 |
| `plugin.config_version` | string | `"0.1.0"` | 配置版本号，新增字段时提升；缺失会被宿主拒绝加载配置 |
| `access.readable_streams` | list[string] | `[]` | 可读群号名单；留空等于没有任何群能看到工具 |
| `access.private_enabled` | bool | `false` | 私聊历史总闸；与白名单内容无关 |
| `recent.default_limit` | int | `20` | LLM 不传 `limit` 时的条数 |
| `recent.max_limit` | int | `50` | 单次返回条数上限，超出按上限钳制 |
| `recent.include_bot_messages` | bool | `false` | 是否把机器人自己的发言计入结果 |
| `summary.window_hours` | int | `6` | 摘要新鲜度窗口与首次概括回溯小时数 |
| `summary.max_input_messages` | int | `200` | 喂给 planner 概括的消息条数上限 |

> 💡 宿主按 `plugin.config_version` 决定是否把新增字段写回 `config.toml`，所以升级插件后如果配置里少了字段，先确认这个值有没有跟着提升。

### 数据目录

```
data/plugins/arcdent.read-history-plugin/
└── summaries.sqlite3     # 中期摘要库，一张 summaries 表
```

运行时文件一律走宿主授予的 `data_dir`，插件不自行拼接绝对路径。

---

## 📁 项目结构

```
read-history-plugin/
├── plugins/read_history_plugin/      # 放进 MaiBot plugins/ 的那一份
│   ├── _manifest.json                # 插件清单：ID、版本、依赖能力、版本区间
│   ├── plugin.py                     # 入口：插件类、三个工具、暴露面覆写、生命周期
│   ├── config.toml                   # 随插件发布的默认配置
│   ├── config.py                     # 强类型配置模型（四节）
│   ├── config_rules.py               # 白名单条目的合法性判定（单一定义处）
│   ├── access_gate.py                # 门禁判定与配置变更重载调度
│   ├── stream_resolver.py            # 群号/QQ 号与宿主 session_id 的解析互转
│   ├── history_reader.py             # 消息渲染、条数钳制与读取能力胶水
│   ├── summary_generator.py          # 摘要 prompt 构建与 planner 调用
│   ├── summary_store.py              # 自建 sqlite 摘要存储
│   ├── target_params.py              # 目标参数解析与四类错误文案
│   └── coercion.py                   # 取值规整与路径安全的小工具
├── tests/                            # 279 个用例，纯逻辑与全链路分层
├── CONTEXT.md                        # 领域术语表
├── docs/adr/                         # 架构决策记录（白名单语义、私聊定位、摘要归属）
└── openspec/specs/                   # 三份需求规格（工具、门禁、摘要生成）
```

---

## ❓ 常见问题

### Q: 群里看不到本插件的工具

**A:** 依次确认三件事：

1. 插件的 `enabled` 是否为 `true`；
2. 当前群的群号是否在 `readable_streams` 里；
3. 填完白名单后是否保存过配置——暴露面靠一次自动重载重建，新群需要这次重载才会拿到工具。

### Q: 白名单里写了 QQ 号，为什么没有效果？

**A:** 这是有意的：白名单只装群号。白名单与 QQ 号都是纯数字串，插件无法从格式上区分两者，所以私聊可读性只由 `private_enabled` 一个布尔条件决定，避免同一个数字串在不同场景下语义双关。详见 `docs/adr/0003-private-readability-switch-only.md`。

### Q: 在私聊里问机器人「某群最近聊了什么」，为什么没触发工具？

**A:** 私聊里不暴露任何工具，这是设计取舍而非故障。要读历史必须在白名单群里发问。详见 `docs/adr/0002-private-chats-read-only-targets.md`。

### Q: 白名单里的群号明明是对的，清单里却没显示

**A:** 只有经宿主会话表确认过是群的条目才会作为可读目标出现；宿主里没有该群会话记录（群号写错、或该群从没和机器人产生会话）时不会出现，清单末尾会提示有几个这样的条目。

### Q: 传了 `limit = 200`，为什么只返回 50 条？

**A:** 条数受 `recent.max_limit` 钳制，超出按上限执行且不报错。要读更多就调大这个配置。

### Q: 摘要为什么说「暂时没有可用的中期摘要」或「已过期」？

**A:** 说明本次生成没成功，可能原因有三种：概括时间范围内没有消息、planner 模型调用失败、摘要库不可用。旧摘要不会被删除或改写，稍后再调一次会重新尝试。

### Q: 读出来的消息里有 `[非文本消息]` 是怎么回事？

**A:** 该条消息没有纯文本内容（图片、表情等），插件用占位符保留它在时间线上的位置，而不是整条丢弃。

### Q: 机器人自己的发言也会被读进去吗？

**A:** 默认不会，`recent.include_bot_messages` 为 `false` 时按宿主侧过滤掉机器人发言。

### Q: 配置里白名单写成全角数字会怎样？

**A:** 会在配置期整份拒绝并提示是第几个条目，因为全角数字、带空白的文本都无法命中宿主的会话标识，属于配了不生效的坑。合法白名单在配置期就统一去空白去重。

### Q: 摘要数据存在哪里，要自己清理吗？

**A:** 存在 `data/plugins/arcdent.read-history-plugin/summaries.sqlite3`，与宿主数据库完全隔离。插件不自动清理，重启也不会丢，体积管理归你。

---

## 🛠️ 开发与测试

### 运行测试

测试分两层：九个纯逻辑模块不 import `maibot_sdk`，任意 Python 3 都能跑；六个模块需要真实 SDK，必须用装了 `maibot-plugin-sdk` 的解释器。

```bash
# 全量（需要装了 SDK 的 Python 3.11+ 解释器）
python -m unittest discover -s tests -t tests

# 只跑不依赖 SDK 的那部分（任意 Python 3）
python -m unittest discover -s tests -t tests -p "test_history_reader.py"
```

测试以宿主的加载形态复现插件：把 `plugin.py` 当包根加载，`ctx` 的各能力用鸭子类型桩驱动，所以不起 MaiBot 也能跑完工具全链路。

### 代码风格

本仓库用 [Ruff](https://docs.astral.sh/ruff/) 检查风格（未附自定义配置，走默认规则）：

```bash
uvx ruff check .
```

### 提交信息

提交信息用中文，不写 `feat:`/`fix:` 这类前缀，说明改了什么、为什么改。

### 相关文档

| 文档 | 内容 |
|:-----|:-----|
| `CONTEXT.md` | 领域术语表：聊天流、白名单、私聊开关、中期摘要、工具暴露 |
| `docs/adr/` | 架构决策记录：摘要为什么自建、私聊为什么只作目标、白名单为什么只装群号 |
| `openspec/specs/` | 三份需求规格：工具行为、门禁规则、摘要生成 |
| `docs/maibot-新体系-跨群历史读取-调研.md` | 宿主机制调研：会话标识、按会话过滤工具、能力代理 |

---

## 📄 许可证

本项目采用 [MIT](LICENSE) 许可证开源，与 `_manifest.json` 的 `license` 字段一致。

---

## 🙏 致谢

- [Mai-with-u/MaiBot](https://github.com/Mai-with-u/MaiBot) - QQ 机器人框架
- [Mai-with-u/plugin-repo](https://github.com/Mai-with-u/plugin-repo) - MaiBot 插件中心

---

<div align="center">

**Star ⭐ 如果这个插件帮到了你！**

</div>

<!-- markdownlint-restore -->
