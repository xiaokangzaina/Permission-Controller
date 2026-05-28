![权限控制器](https://raw.githubusercontent.com/xiaokangzaina/astrbot_plugin_permission_controller/main/plugin-avatar-v1.6.0.png)

# 权限控制器

用于 AstrBot 的群聊与私聊权限控制插件。

![License](https://img.shields.io/badge/License-AGPLv3-blue)
![Python](https://img.shields.io/badge/Python-3.10%2B-green)
![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-purple)
![Version](https://img.shields.io/badge/Version-v1.7.9-orange)

---

## 项目介绍

本插件用于在消息进入模型或其他插件前，按规则控制谁可以调用机器人。

它解决的是“谁能使用机器人”的问题，不是“谁是管理员”的问题。

私聊白名单用户只会被允许私聊机器人，不会获得 AstrBot 管理员权限，也不会获得管理员指令权限。

---

## 核心功能

- 私聊白名单
- 群聊用户-群号组合放行
- 群聊整体放行
- 群聊用户黑名单
- AstrBot 管理员绕过限制
- AstrBot 管理员绕过唤醒词
- 群聊放行列表同步到 AstrBot 平台白名单
- 兼容 AstrBot 核心白名单阶段，避免私聊白名单被核心提前拦截

---

## 重要变更：v1.7.9 已移除 Web 面板

从 v1.7.9 开始，本插件不再提供前端 Web 设置面板。

原因：不同 AstrBot Dashboard/插件页 bridge 的路由兼容存在差异，可能导致插件面板保存失败，例如：

保存失败：Request failed with status code 500；保存失败：未找到该路由

为保证插件稳定性，已移除：

- pages/settings 目录
- web.py
- page_service.py
- Web API 注册逻辑

配置请改用 AstrBot 插件配置页，或直接编辑配置文件。

---

## 安装方式

推荐下载 Release 安装包：

安装包名称：astrbot_plugin_permission_controller-v1.7.9.zip

Release 地址：

https://github.com/xiaokangzaina/astrbot_plugin_permission_controller/releases/tag/v1.7.9

在 AstrBot 插件管理页面选择本地 ZIP 安装即可。

---

## 配置方式

配置文件路径：

配置文件位于 data/config 目录下，文件名为 astrbot_plugin_permission_controller_config.json。

修改配置后，建议重载插件或重启 AstrBot。

配置时请按字段含义填写：管理员相关字段使用开关值，私聊白名单、群聊放行、用户群号组合规则和群聊黑名单使用列表值。

---

## 配置项说明

| 配置项 | 类型 | 说明 |
| :--- | :--- | :--- |
| admin_bypass | bool | 管理员绕过限制。开启后，AstrBot 全局管理员不会被本插件拦截。 |
| admin_wake_bypass | bool | 管理员绕过唤醒词。开启后，管理员普通消息也可唤醒机器人。 |
| private_chat_users | list | 私聊白名单。填写允许私聊机器人的普通用户 QQ。 |
| enable_group_rules | bool | 是否启用群聊调用权限规则。 |
| simple_rules | list | 用户-群号组合放行规则，格式：用户 QQ 与群号组合。 |
| allowed_groups | list | 群聊整体放行列表。填写群号。 |
| enable_group_blacklist | bool | 是否启用群聊用户黑名单。 |
| group_blacklist | list | 群聊禁止调用黑名单。填写用户 QQ，不是群号。 |

---

## 权限逻辑

### 私聊

普通用户只有在 private_chat_users 中时，才允许私聊机器人。

管理员是否绕过，由 admin_bypass 控制。

### 群聊

群聊检查顺序：

1. 如果启用黑名单，且用户在 group_blacklist 中，则拦截。
2. 如果 admin_bypass 开启，且用户是 AstrBot 管理员，则放行。
3. 如果 enable_group_rules 关闭，则放行。
4. 如果群号在 allowed_groups 中，则整个群放行。
5. 如果命中 simple_rules 中的 用户 QQ 与群号组合，则放行。
6. 其余情况拦截。

---

## 推荐配置场景

### 只允许指定好友私聊

把允许使用机器人的好友 QQ 填入私聊白名单。

### 放行整个群

把需要整体放行的群号填入群聊整体放行列表。

### 只允许某人在某群使用

在用户群号组合规则中填写“用户 QQ 加群号”的组合规则，表示该用户只能在指定群内调用机器人。

### 禁止某人在任何群聊调用

开启群聊黑名单功能，并把需要禁止调用机器人的用户 QQ 填入群聊黑名单。

---

## 关于平台白名单同步

allowed_groups 会在插件启动时同步到 AstrBot 平台白名单 ID 列表。

这是为了避免 AstrBot 核心白名单阶段早于插件执行，导致群消息还没进入插件就被核心拦截。

如果你从 allowed_groups 删除群号，插件也会尝试从平台白名单中同步移除由本插件添加的群号，同时保留你手动配置的其他平台白名单。

---

## 常见问题

### 为什么 v1.7.9 没有 Web 面板？

因为 Web 面板在部分 AstrBot Dashboard 环境下会出现路由不兼容，导致保存失败。v1.7.9 起已移除，改用插件配置页或配置文件维护。

### 私聊白名单会让用户变成管理员吗？

不会。私聊白名单只表示允许私聊机器人，不授予任何管理员权限。

### 群聊黑名单应该填群号吗？

不是。group_blacklist 填用户 QQ，表示禁止该用户在群聊中调用机器人。

### 修改配置后为什么没立即生效？

请重载插件或重启 AstrBot。涉及平台白名单同步时，重启更稳妥。

### 群临时会话会按私聊白名单放行吗？

不会。群临时会话不等同于普通好友私聊，为避免误放行，插件不会把它当成私聊白名单处理。

---

## 更新记录

### v1.7.9

- 移除前端 Web 设置面板。
- 移除 Web API 保存逻辑。
- 改回使用 AstrBot 插件配置页或配置文件维护配置。
- 避免 Dashboard 插件页路由兼容问题导致保存失败。

### v1.7.8

- 修复 Web 面板保存配置时可能返回 500 的问题。
- 兼容不同 AstrBot 版本的插件配置保存接口。

### v1.7.7

- 新增前端 Web 设置面板。

### v1.7.6

- 平台白名单改为双向同步。

### v1.7.5

- 修复私聊白名单识别问题。
- 给 AstrBot 核心白名单阶段加入运行时兼容补丁。

### v1.7.4

- 修复私聊白名单用户不生效。

### v1.7.3

- 修正 AstrBot 版本要求为 >=4.0.0,<5。

### v1.7.2

- 修复关闭管理员绕过唤醒词后旧补丁仍在内存中生效的问题。

### v1.7.0

- 新增管理员绕过唤醒词开关。

---

## 许可证

本插件遵循仓库中的 LICENSE 文件。
