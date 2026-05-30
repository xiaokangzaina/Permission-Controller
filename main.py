"""AstrBot 权限控制器插件。

本插件负责在 AstrBot 消息事件进入模型或其他插件前，按私聊白名单、
群聊整体放行列表、用户-群号组合规则和群聊黑名单进行权限拦截。
代码中的注释重点说明拦截顺序、兼容逻辑和对 AstrBot 运行时配置的最小改动。
"""

import json
import logging
from pathlib import Path
from sys import maxsize

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.platform.message_type import MessageType

try:
    from .web import PermissionWebController
except Exception:  # pragma: no cover - web 模块缺失时不影响核心功能
    PermissionWebController = None

logger = logging.getLogger(__name__)

class _AstrBotAfterMessageSentLogFilter(logging.Filter):
    """仅屏蔽 after_message_sent 终止传播的冗余日志，不影响发送消息日志。"""

    TARGET_TEXT = "astrbot - after_message_sent 终止了事件传播。"

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            return self.TARGET_TEXT not in record.getMessage()
        except Exception:
            return True



@register(
    "astrbot_plugin_permission_controller",
    "local",
    "按 用户QQ-群号/群号列表 限制谁能调用模型/机器人",
    "1.8.3",
)
class GroupUserWhitelistPlugin(Star):
    """AstrBot 权限控制器主类。

    拦截策略：
    1. 群聊先检查黑名单，再检查管理员、群整体放行和用户-群号组合。
    2. 私聊只允许配置在 private_chat_users 中的普通用户；管理员可按配置绕过。
    3. allowed_groups 会同步到 AstrBot 平台白名单，避免核心层提前拦截群消息。
    """

    _after_message_sent_log_filter_installed = False
    _after_message_sent_log_filter = _AstrBotAfterMessageSentLogFilter()
    _admin_wake_bypass_patch_installed = False
    _whitelist_stage_patch_installed = False

    def __init__(self, context: Context, config=None):
        """初始化插件配置、规则缓存和运行时兼容补丁。"""
        super().__init__(context)
        self.context = context
        self.config = config or {}
        self.rules = self._load_rules()
        self.admin_bypass = self._get_bool_config("admin_bypass", True)
        self.admin_wake_bypass = self._get_bool_config("admin_wake_bypass", False)
        self.enable_group_rules = self._get_bool_config("enable_group_rules", True)
        self.enable_group_blacklist = self._get_bool_config(
            "enable_group_blacklist", True
        )
        self.admin_ids = self._load_admin_ids()
        self.group_blacklist = self._normalize_ids(self._cfg_get("group_blacklist", []))
        self.private_chat_users = self._normalize_ids(
            self._cfg_get("private_chat_users", [])
        )
        self.allowed_groups = self._normalize_ids(self._cfg_get("allowed_groups", []))
        self._sync_plugin_allowlist_to_platform_whitelist()
        self._install_after_message_sent_log_filter()
        self._install_admin_wake_bypass_patch()
        self._install_private_whitelist_stage_patch()
        logger.info(
            "[PermissionController] 已加载：群聊规则=%s，群整体放行=%s，用户群号规则=%s，私聊白名单=%s，群聊黑名单=%s，管理员绕过=%s",
            self.enable_group_rules,
            sorted(self.allowed_groups),
            self.rules,
            sorted(self.private_chat_users),
            sorted(self.group_blacklist),
            self.admin_bypass,
        )

        self._register_web_page()


    def _register_web_page(self):
        """注册可视化配置页（Web 前端）。失败不影响核心拦截功能。"""
        if PermissionWebController is None:
            return
        try:
            self.web = PermissionWebController(self.context, self)
            self.web.register_routes()
            logger.info("[PermissionController] 配置页 Web API 已注册")
        except Exception as exc:
            logger.warning("[PermissionController] 配置页注册失败: %s", exc)

    def reload_runtime_config(self):
        """供配置页保存后调用：重新读取配置并刷新运行时缓存。"""
        self.rules = self._load_rules()
        self.admin_bypass = self._get_bool_config("admin_bypass", True)
        self.admin_wake_bypass = self._get_bool_config("admin_wake_bypass", False)
        self.enable_group_rules = self._get_bool_config("enable_group_rules", True)
        self.enable_group_blacklist = self._get_bool_config(
            "enable_group_blacklist", True
        )
        self.group_blacklist = self._normalize_ids(self._cfg_get("group_blacklist", []))
        self.private_chat_users = self._normalize_ids(
            self._cfg_get("private_chat_users", [])
        )
        self.allowed_groups = self._normalize_ids(self._cfg_get("allowed_groups", []))
        try:
            self._sync_plugin_allowlist_to_platform_whitelist()
        except Exception as exc:
            logger.warning("[PermissionController] 同步平台白名单失败: %s", exc)
        logger.info("[PermissionController] 运行时配置已重载")

    @classmethod
    def _install_after_message_sent_log_filter(cls):
        """安装精确日志过滤器，只屏蔽 after_message_sent 终止传播日志。"""
        if cls._after_message_sent_log_filter_installed:
            return
        target_loggers = [
            logging.getLogger(),
            logging.getLogger("astrbot"),
            logging.getLogger("Core"),
            logging.getLogger("core"),
            logging.getLogger("astrbot.core"),
            logging.getLogger("astrbot.core.pipeline.context_utils"),
        ]
        for lg in target_loggers:
            try:
                lg.addFilter(cls._after_message_sent_log_filter)
                for handler in getattr(lg, "handlers", []) or []:
                    handler.addFilter(cls._after_message_sent_log_filter)
            except Exception:
                pass
        cls._after_message_sent_log_filter_installed = True

    _PRIVATE_CONFIG_KEYS = {"private_chat_users", "admin_bypass", "admin_wake_bypass"}
    _GROUP_CONFIG_KEYS = {
        "enable_group_rules",
        "simple_rules",
        "allowed_groups",
        "enable_group_blacklist",
        "group_blacklist",
    }

    @classmethod
    def _config_group_for_key(cls, key: str) -> str | None:
        if key in cls._PRIVATE_CONFIG_KEYS:
            return "private_chat_settings"
        if key in cls._GROUP_CONFIG_KEYS:
            return "group_chat_settings"
        return None

    @classmethod
    def _dict_cfg_get(cls, data: dict, key: str, default=None):
        if not isinstance(data, dict):
            return default
        if key in data:
            return data.get(key, default)
        group_name = cls._config_group_for_key(key)
        group = data.get(group_name, {}) if group_name else {}
        if isinstance(group, dict) and key in group:
            return group.get(key, default)
        return default

    def _cfg_get(self, key, default=None):
        """安全读取配置，兼容旧版平铺配置和新版分组配置。"""
        try:
            if hasattr(self.config, "get"):
                value = self.config.get(key, None)
                if value is not None:
                    return value
                group_name = self._config_group_for_key(key)
                if group_name:
                    group = self.config.get(group_name, {})
                    if isinstance(group, dict) and key in group:
                        return group.get(key, default)
        except Exception:
            pass
        if isinstance(self.config, dict):
            return self._dict_cfg_get(self.config, key, default)
        return default

    def _get_bool_config(self, key, default=False):
        """兼容布尔值和中文/英文字符串形式的开关配置。"""
        value = self._cfg_get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
                "开启",
                "开",
                "启用",
            )
        return bool(value)

    @classmethod
    def _load_runtime_private_chat_users(cls) -> set[str]:
        """运行时读取私聊白名单，供核心白名单阶段补丁使用。"""
        try:
            cfg_path = (
                Path(__file__).resolve().parents[2]
                / "config"
                / "astrbot_plugin_permission_controller_config.json"
            )
            if not cfg_path.exists():
                return set()
            data = json.loads(cfg_path.read_text(encoding="utf-8-sig") or "{}")
            users = cls._dict_cfg_get(data, "private_chat_users", [])
            if isinstance(users, (str, int)):
                users = [users]
            if not isinstance(users, list):
                return set()
            return {str(item).strip() for item in users if str(item).strip()}
        except Exception as exc:
            logger.debug(f"读取私聊白名单运行时配置失败: {exc}")
            return set()

    @classmethod
    def _install_private_whitelist_stage_patch(cls):
        """让核心 WhitelistCheckStage 识别本插件私聊白名单。

        AstrBot 的 WhitelistCheckStage 早于插件 handler 执行，并且只检查
        unified_msg_origin/群号；当平台白名单启用时，仅填写 QQ 号或在
        本插件 private_chat_users 中填写用户，都可能被核心阶段提前拦截。
        这里在私聊消息下收集 sender/session/UMO 分段候选 ID，命中本插件
        private_chat_users 时直接放行核心白名单阶段。
        """
        try:
            from astrbot.core.pipeline.whitelist_check.stage import WhitelistCheckStage
        except Exception as exc:
            logger.debug(f"安装私聊白名单核心阶段补丁失败: {exc}")
            return

        if getattr(
            WhitelistCheckStage, "_permission_controller_patch_installed", False
        ):
            return

        original_process = WhitelistCheckStage.process
        WhitelistCheckStage._permission_controller_original_process = original_process

        async def patched_process(stage_self, event):
            try:
                if event.get_message_type() == MessageType.FRIEND_MESSAGE:
                    users = cls._load_runtime_private_chat_users()
                    if (
                        users
                        and cls._private_sender_candidates_from_event(event) & users
                    ):
                        return
            except Exception as exc:
                logger.debug(f"私聊白名单核心阶段补丁判断失败: {exc}")
            result = original_process(stage_self, event)
            return await result

        WhitelistCheckStage.process = patched_process
        WhitelistCheckStage._permission_controller_patch_installed = True

    @staticmethod
    def _private_sender_candidates_from_event(event: AstrMessageEvent) -> set[str]:
        """从事件对象提取私聊用户候选 ID，供插件逻辑和核心阶段补丁复用。"""
        candidates = set()
        for getter_name in ("get_sender_id", "get_session_id"):
            try:
                getter = getattr(event, getter_name, None)
                if callable(getter):
                    value = getter()
                    if value is not None and str(value).strip():
                        candidates.add(str(value).strip())
            except Exception:
                pass

        for attr_path in (
            ("message_obj", "sender", "user_id"),
            ("message_obj", "session_id"),
            ("session", "session_id"),
        ):
            try:
                obj = event
                for attr in attr_path:
                    obj = getattr(obj, attr)
                if obj is not None and str(obj).strip():
                    candidates.add(str(obj).strip())
            except Exception:
                pass

        try:
            umo = str(event.unified_msg_origin or "").strip()
            if umo:
                candidates.add(umo)
                for sep in (":", "!"):
                    if sep in umo:
                        for part in umo.split(sep):
                            part = part.strip()
                            if part:
                                candidates.add(part)
        except Exception:
            pass
        return {x for x in candidates if x}

    @classmethod
    def _install_admin_wake_bypass_patch(cls):
        """按配置让 AstrBot 管理员绕过唤醒词。

        AstrBot 的唤醒词检查发生在普通插件 handler 之前，因此这里对
        WakingCheckStage.process 做最小猴补丁。补丁会在每条消息到来时实时读取
        插件配置；只有 admin_wake_bypass=true 时才给管理员临时追加内部唤醒前缀。
        关闭配置后，新消息不会再绕过唤醒词。
        """
        try:
            from astrbot.core.pipeline.waking_check.stage import WakingCheckStage
        except Exception as exc:
            logger.debug(f"安装管理员绕过唤醒词补丁失败: {exc}")
            return

        internal_prefix = "__admin_wake_bypass__ "

        def _runtime_enabled() -> bool:
            """运行时读取开关，确保后台改配置后无需重启即可生效。"""
            try:
                cfg_path = (
                    Path(__file__).resolve().parents[2]
                    / "config"
                    / "astrbot_plugin_permission_controller_config.json"
                )
                if cfg_path.exists():
                    raw = cfg_path.read_text(encoding="utf-8-sig")
                    data = json.loads(raw) if raw.strip() else {}
                    value = cls._dict_cfg_get(data, "admin_wake_bypass", False)
                    if isinstance(value, bool):
                        return value
                    if isinstance(value, str):
                        return value.strip().lower() in (
                            "1",
                            "true",
                            "yes",
                            "on",
                            "开启",
                            "开",
                            "启用",
                        )
                    return bool(value)
            except Exception as exc:
                logger.debug(f"读取管理员绕过唤醒词配置失败: {exc}")
            return False

        if getattr(WakingCheckStage, "_permission_controller_patch_installed", False):
            WakingCheckStage._permission_controller_runtime_enabled = staticmethod(
                _runtime_enabled
            )
            return

        original_process = WakingCheckStage.process
        WakingCheckStage._permission_controller_original_process = original_process
        WakingCheckStage._permission_controller_runtime_enabled = staticmethod(
            _runtime_enabled
        )

        async def patched_process(self, event):
            """在管理员消息前临时插入内部唤醒词，再交回原始唤醒流程。"""
            added_prefix = False
            original_message_str = None
            try:
                wake_prefixes = self.ctx.astrbot_config.setdefault("wake_prefix", [])
                if internal_prefix in wake_prefixes:
                    # 防止内部前缀残留在全局唤醒词里，导致用户手动输入该前缀也能触发。
                    wake_prefixes[:] = [
                        x for x in wake_prefixes if x != internal_prefix
                    ]

                enabled = WakingCheckStage._permission_controller_runtime_enabled()
                if enabled:
                    admins = {
                        str(x).strip()
                        for x in self.ctx.astrbot_config.get("admins_id", [])
                        if str(x).strip()
                    }
                    sender_id = str(event.get_sender_id() or "").strip()
                    if sender_id and sender_id in admins:
                        event.role = "admin"
                        wake_prefixes.append(internal_prefix)
                        if not str(event.message_str or "").startswith(internal_prefix):
                            original_message_str = event.message_str
                            event.message_str = internal_prefix + str(
                                event.message_str or ""
                            )
                            added_prefix = True
            except Exception as exc:
                logger.debug(f"管理员绕过唤醒词处理失败，回退默认唤醒检查: {exc}")

            await WakingCheckStage._permission_controller_original_process(self, event)

            if added_prefix:
                try:
                    event.message_str = original_message_str or event.message_str
                    if hasattr(event, "message_obj"):
                        event.message_obj.message_str = event.message_str
                except Exception:
                    pass

        WakingCheckStage.process = patched_process
        WakingCheckStage._permission_controller_patch_installed = True
        cls._admin_wake_bypass_patch_installed = True

    @staticmethod
    def _normalize_ids(value):
        """把配置中的 ID 列表统一转换为去空白字符串集合。"""
        if value is None:
            return set()
        if isinstance(value, (str, int)):
            value = [value]
        if not isinstance(value, list):
            return set()
        return {str(item).strip() for item in value if str(item).strip()}

    def _load_admin_ids(self):
        """从 AstrBot 全局配置读取管理员 ID，用于绕过权限限制。"""
        admin_ids = set()
        try:
            global_config = self.context.get_config()
            if hasattr(global_config, "get"):
                admin_ids.update(
                    self._normalize_ids(global_config.get("admins_id", []))
                )
        except Exception:
            pass
        return admin_ids

    def _plugin_synced_ids_path(self) -> Path:
        """记录本插件已同步到平台白名单的 ID，避免误删手动平台白名单。"""
        return (
            Path(__file__).resolve().parents[2]
            / "config"
            / "astrbot_plugin_permission_controller_synced_ids.json"
        )

    def _load_plugin_synced_ids(self) -> set[str]:
        """读取历史同步记录。"""
        try:
            path = self._plugin_synced_ids_path()
            if not path.exists():
                return set()
            data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
            ids = data.get("synced_ids", [])
            if isinstance(ids, (str, int)):
                ids = [ids]
            if not isinstance(ids, list):
                return set()
            return self._normalize_ids(ids)
        except Exception as exc:
            logger.debug(f"读取插件同步白名单记录失败: {exc}")
            return set()

    def _save_plugin_synced_ids(self, synced_ids: set[str]) -> None:
        """保存本插件当前负责同步的 ID。"""
        try:
            path = self._plugin_synced_ids_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "synced_ids": sorted(synced_ids),
                        "note": "IDs managed by astrbot_plugin_permission_controller. Manual platform whitelist entries are not recorded here.",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug(f"保存插件同步白名单记录失败: {exc}")

    def _merge_platform_whitelist(
        self, current: set[str], plugin_allowlist: set[str]
    ) -> list[str]:
        """合并平台白名单。

        规则：
        - 插件当前配置中的 ID 必须存在于平台白名单；
        - 插件历史同步过但现在已从插件配置删除的 ID，从平台白名单移除；
        - 不在历史同步记录中的平台 ID 视为用户手动维护，保留不动。
        """
        previous_synced = self._load_plugin_synced_ids()
        manual_or_external = current - previous_synced
        return sorted(manual_or_external | plugin_allowlist)

    def _sync_plugin_allowlist_to_platform_whitelist(self) -> None:
        """把插件放行对象双向同步到 AstrBot 平台 ID 白名单。

        AstrBot 核心平台白名单检查早于普通插件 handler。这里同步
        private_chat_users 和 allowed_groups 到平台 id_whitelist。
        删除插件配置中的 ID 时，也会从平台白名单移除；但只移除本插件
        历史同步过的 ID，避免误删用户手动添加的平台白名单。
        """
        plugin_allowlist = self.allowed_groups | self.private_chat_users

        try:
            global_config = self.context.get_config()
        except Exception:
            global_config = None

        # 1. 尝试修改运行时配置对象。
        try:
            if hasattr(global_config, "get"):
                current = self._normalize_ids(global_config.get("id_whitelist", []))
                merged = self._merge_platform_whitelist(current, plugin_allowlist)
                if hasattr(global_config, "set"):
                    global_config.set("id_whitelist", merged)
                elif isinstance(global_config, dict):
                    global_config["id_whitelist"] = merged
        except Exception as exc:
            logger.debug(f"同步插件放行列表到运行时平台白名单失败: {exc}")

        # 2. 同步写入 data/cmd_config.json，便于重启后继续生效。
        try:
            data_dir = Path(__file__).resolve().parents[2]
            cmd_config_path = data_dir / "cmd_config.json"
            if cmd_config_path.exists():
                raw = cmd_config_path.read_text(encoding="utf-8-sig")
                data = json.loads(raw) if raw.strip() else {}
                platform_settings = data.setdefault("platform_settings", data)
                current = self._normalize_ids(platform_settings.get("id_whitelist", []))
                merged = self._merge_platform_whitelist(current, plugin_allowlist)
                if merged != list(platform_settings.get("id_whitelist", [])):
                    platform_settings["id_whitelist"] = merged
                    cmd_config_path.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
        except Exception as exc:
            logger.debug(f"同步插件放行列表到 cmd_config.json 失败: {exc}")

        # 3. 最后更新同步记录。即使插件列表为空，也要记录为空，
        #    这样下一次能确认旧同步项已被插件释放。
        self._save_plugin_synced_ids(plugin_allowlist)

    def _load_rules(self):
        """解析 用户QQ-群号 规则，生成 group_id -> allowed_user_ids 映射。"""
        raw_rules = self._cfg_get("simple_rules", [])
        if not isinstance(raw_rules, list):
            raw_rules = []
        if not raw_rules:
            return {}
        rules = {}
        for item in raw_rules:
            item = str(item).strip()
            if not item or "-" not in item:
                continue
            user_id, group_id = item.split("-", 1)
            user_id = user_id.strip()
            group_id = group_id.strip()
            if not user_id or not group_id:
                continue
            if not user_id.isdigit() or not group_id.isdigit():
                continue
            rules.setdefault(group_id, set()).add(user_id)
        return rules

    def _is_admin(self, sender_id: str) -> bool:
        """判断发送者是否是 AstrBot 全局管理员。"""
        sender_id = str(sender_id).strip()
        return bool(sender_id and sender_id in self.admin_ids)

    @staticmethod
    def _extract_tail_ids_from_unified_origin(umo: str) -> set[str]:
        """从 unified_msg_origin 中尽量提取可能的私聊用户 ID。"""
        result = set()
        text = str(umo or "").strip()
        if not text:
            return result
        result.add(text)
        # 常见：platform:FriendMessage:session 或 webchat:FriendMessage:webchat!user!cid
        for sep in (":", "!"):
            if sep in text:
                for part in text.split(sep):
                    part = part.strip()
                    if part:
                        result.add(part)
        return result

    def _private_sender_candidates(self, event: AstrMessageEvent) -> set[str]:
        """私聊 ID 兼容：QQ号、sender.user_id、session_id、unified_msg_origin 分段都参与匹配。"""
        return self._private_sender_candidates_from_event(event)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=maxsize)
    async def check_group_user_whitelist(self, event: AstrMessageEvent):
        """群聊权限入口：黑名单优先，其次按管理员和放行规则判断。"""
        group_id = str(event.get_group_id() or "").strip()
        sender_id = str(event.get_sender_id() or "").strip()

        # 黑名单优先级最高；但允许平台管理员按 admin_bypass 配置绕过。
        if self.enable_group_blacklist and sender_id in self.group_blacklist:
            if self.admin_bypass and self._is_admin(sender_id):
                return
            event.stop_event()
            return

        if not self.enable_group_rules:
            return

        # 严格群聊权限：群聊规则开启后，只有两种情况放行：
        # 1. 群号在“放行权限 QQ 群聊列表”中，整个群放行；
        # 2. 命中“放行权限 QQ 列表”的 用户QQ-群号 组合。
        # 未配置的群、未配置的用户一律拦截，避免“未填写群号仍可调用”。
        if self.admin_bypass and self._is_admin(sender_id):
            return

        if group_id in self.allowed_groups:
            return

        allowed_users = self.rules.get(group_id, set())
        if sender_id and sender_id in allowed_users:
            return

        event.stop_event()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=maxsize)
    async def check_private_chat_whitelist(self, event: AstrMessageEvent):
        """私聊白名单。

        使用 ALL + event.is_private_chat() 兜底，避免部分适配器/版本下
        PRIVATE_MESSAGE 过滤器未命中导致私聊白名单不生效。
        """
        try:
            if not event.is_private_chat():
                return
        except Exception:
            # 兜底：如果无法判断为私聊，不拦截，避免误伤群聊。
            return

        # 不同适配器暴露的私聊 ID 字段不同，因此收集多个候选值做交集匹配。
        candidates = self._private_sender_candidates(event)
        if not candidates:
            event.stop_event()
            return

        if self.admin_bypass and any(self._is_admin(item) for item in candidates):
            return

        # 私聊白名单为空时，表示不放行任何普通私聊用户。
        if self.private_chat_users and candidates & self.private_chat_users:
            return

        event.stop_event()
