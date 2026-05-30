![权限控制器](https://raw.githubusercontent.com/xiaokangzaina/astrbot_plugin_permission_controller/main/logo.png)

# 权限控制器

用于 AstrBot 的私聊与群聊调用权限控制插件。

![License](https://img.shields.io/badge/License-AGPLv3-blue)
![Python](https://img.shields.io/badge/Python-3.10%2B-green)
![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-purple)
![Version](https://img.shields.io/badge/Version-v1.9.2-orange)

---

## 项目介绍

权限控制器用于在消息进入模型或其他插件前，判断当前用户、群聊是否允许调用机器人。

它只解决“谁可以使用机器人”的问题，不授予 AstrBot 管理员权限，也不会修改 AstrBot 的管理员列表。

适合这些场景：

- 只允许指定好友私聊机器人；
- 只允许指定群整体使用机器人；
- 只允许某个用户在某个群里使用机器人；
- 禁止指定 QQ 号在群聊中调用机器人；
- 避免 AstrBot 核心白名单提前拦截已放行群聊。

---

## 核心功能

- 私聊白名单；
- 群聊整体放行；
- 用户 QQ + 群号组合放行；
- 群聊用户黑名单；
- AstrBot 管理员绕过权限限制；
- AstrBot 管理员绕过唤醒词；
- 自动同步群聊放行列表到 AstrBot 平台白名单；
- Web 可视化配置页；
- 群聊头像、群名、群号卡片展示；
- 群列表搜索与手动同步；
- 单群权限配置；
- 最近更改配置的群聊优先排序；
- 浅色、深色、自动主题切换。

---

## v1.9.2 重要变更

- 升级版本号到 `v1.9.2`；
- Web 群列表支持按“最近更改配置”优先排序；
- 保存或重置单群配置后，会记录该群最近配置时间；
- 群列表接口返回 `config_updated_at`，前端优先使用后端排序数据；
- 进入 Web 配置页时自动同步群列表；
- 调整配置页布局，使页面更适合宽屏 Dashboard；
- 重写 README，移除旧版外部插件引用说明。

> 说明：历史配置没有最近更改时间。升级后保存或重置过的群，才会进入最近配置排序。

---

## 安装方式

推荐下载 Release 安装包：

```text
astrbot_plugin_permission_controller-v1.9.2.zip
```

Release 地址：

```text
https://github.com/xiaokangzaina/astrbot_plugin_permission_controller/releases/tag/v1.9.2
```

在 AstrBot 插件管理页面选择本地 ZIP 安装即可。

也可以在 AstrBot 插件目录中克隆仓库：

```bash
git clone https://github.com/xiaokangzaina/astrbot_plugin_permission_controller.git
```

---

## 支持平台

`metadata.yaml` 当前声明支持：

- `qq_official`
- `aiocqhttp`

实时群列表依赖平台接口能力。若接口不可用，Web 页面会回退显示已配置过的群号。

---

## Web 配置页使用

打开 AstrBot Dashboard，进入插件管理，找到“权限控制器”，打开插件配置页面。

操作流程：

1. 页面打开后会自动同步群列表；
2. 也可以点击“同步群列表”手动刷新；
3. 在左侧搜索或选择目标群；
4. 在右侧开启“整群放行”，或填写“本群允许用户”；
5. 点击“保存该群配置”；
6. 最近保存或重置过配置的群会优先显示在列表顶部。

### 单群配置含义

| 页面字段 | 写入配置项 | 说明 |
| :--- | :--- | :--- |
| 整群放行 | `allowed_groups` | 开启后，该群所有成员都可调用机器人。 |
| 本群允许用户 | `simple_rules` | 每行一个 QQ 号，保存为 `用户QQ-群号`。 |

---

## 配置文件方式

配置文件路径：

```text
data/config/astrbot_plugin_permission_controller_config.json
```

手动修改配置文件后，建议重载插件或重启 AstrBot。

---

## 配置项说明

| 配置项 | 类型 | 说明 |
| :--- | :--- | :--- |
| `admin_bypass` | bool | 管理员绕过限制。开启后，AstrBot 全局管理员不会被本插件拦截。 |
| `admin_wake_bypass` | bool | 管理员绕过唤醒词。开启后，管理员普通消息也可唤醒机器人。 |
| `private_chat_users` | list | 私聊白名单。填写允许私聊机器人的普通用户 QQ。 |
| `enable_group_rules` | bool | 是否启用群聊调用权限规则。 |
| `simple_rules` | list | 用户-群号组合放行规则，格式：`用户QQ-群号`。 |
| `allowed_groups` | list | 群聊整体放行列表。填写群号。 |
| `enable_group_blacklist` | bool | 是否启用群聊用户黑名单。 |
| `group_blacklist` | list | 群聊禁止调用黑名单。填写用户 QQ，不是群号。 |

---

## 权限判断逻辑

### 私聊

普通用户只有在 `private_chat_users` 中时，才允许私聊机器人。

如果 `admin_bypass` 开启，AstrBot 管理员可绕过私聊白名单。

### 群聊

群聊检查顺序：

1. 启用黑名单且用户在 `group_blacklist` 中：拦截；
2. `admin_bypass` 开启且用户是 AstrBot 管理员：放行；
3. `enable_group_rules` 关闭：放行；
4. 群号在 `allowed_groups` 中：放行；
5. 命中 `simple_rules` 中的 `用户QQ-群号`：放行；
6. 其他情况：拦截。

---

## 推荐配置场景

### 只允许指定好友私聊

把允许使用机器人的好友 QQ 填入 `private_chat_users`。

### 放行整个群

在 Web 配置页选择群聊后开启“整群放行”，或手动把群号填入 `allowed_groups`。

### 只允许某人在某群使用

在 Web 配置页选择群聊后，在“本群允许用户”中填写用户 QQ。

也可以手动在 `simple_rules` 中填写：

```text
用户QQ-群号
```

### 禁止某人在任何群聊调用

开启 `enable_group_blacklist`，并把需要禁止的用户 QQ 填入 `group_blacklist`。

---

## 关于平台白名单同步

`allowed_groups` 会在插件启动或 Web 保存配置后同步到 AstrBot 平台白名单 ID 列表。

这样可以避免 AstrBot 核心白名单阶段早于插件执行，导致群消息还没进入本插件就被核心拦截。

如果从 `allowed_groups` 删除群号，插件会尝试移除由本插件同步写入的平台白名单项，同时保留你手动配置的其他平台白名单。

---

## 使用权限不是管理员权限

本插件只控制消息是否能进入机器人和后续插件。

私聊白名单用户、群聊整体放行群、用户群号组合规则，只表示“允许使用机器人”。

它们不会：

- 修改 AstrBot 的 `admins_id`；
- 让普通用户变成管理员；
- 授予管理员指令权限。

如果某个功能插件本身也有白名单或权限系统，需要同时满足：

- 权限控制器允许该群或该用户通过；
- 目标插件自己的权限配置允许；
- AstrBot 平台白名单没有提前拦截。

---

## 常见问题

### Web 配置页的“整群放行”对应哪个字段？

对应 `allowed_groups`。

### Web 配置页的“本群允许用户”对应哪个字段？

对应 `simple_rules`，页面会自动保存为 `用户QQ-群号`。

### 群聊黑名单应该填群号吗？

不是。`group_blacklist` 填用户 QQ，表示禁止该用户在群聊中调用机器人。

### 私聊白名单会让用户变成管理员吗？

不会。私聊白名单只表示允许私聊机器人。

### 为什么某些群没有排在最近配置顶部？

只有升级到支持最近配置排序后，保存或重置过单群配置的群才会记录时间。

### 修改配置后为什么没立即生效？

Web 配置页保存后会刷新插件运行时缓存。手动改配置文件时，请重载插件或重启 AstrBot。

### 群临时会话会按私聊白名单放行吗？

不会。群临时会话不等同于普通好友私聊，为避免误放行，插件不会把它当成私聊白名单处理。

---

## 更新记录

### v1.9.2

- 新增群配置更新时间持久记录。
- 群列表按最近配置时间优先排序。
- 前端优先读取后端 `config_updated_at`。
- 页面打开时自动同步群列表。
- 调整 Web 配置页宽屏布局。
- 重写文档并移除外部插件引用。

### v1.9.1

- 修复部分日志噪声问题。

### v1.9.0

- 恢复并重构 Web 可视化配置页。
- 新增群聊头像/卡片式选择界面。
- 新增单群配置：整群放行、本群允许用户。
- 新增群列表同步接口，优先读取 QQ 平台实时群列表，失败时回退到已配置群号。
- Web 保存后自动刷新插件运行时缓存，并继续同步平台白名单。

### v1.8.3

- 明确普通群友和私聊白名单用户只获得插件使用权限，不会获得管理员权限。
- 说明其他插件需要同时配置各自白名单和权限控制器放行。
- 保留配置面板“私聊类/群聊类”分组，以及旧版平铺配置兼容读取。

---
