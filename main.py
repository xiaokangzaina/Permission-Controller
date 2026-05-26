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

logger = logging.getLogger(__name__)


class _AstrBotStopPropagationLogFilter(logging.Filter):
    """屏蔽 AstrBot 框架输出的指定冗余日志。"""

    TARGET_TEXTS = (
        "astrbot - after_message_sent 终止了事件传播。",
        "Prepare to send -",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        """返回 False 时丢弃命中的冗余日志记录。"""
        try:
            msg = record.getMessage()
        except Exception:
            return True
        text = str(msg)
        return not any(target in text for target in self.TARGET_TEXTS)


@register(
    "astrbot_plugin_permission_controller",
    "local",
    "按 用户QQ-群号/群号列表 限制谁能调用模型/机器人",
    "1.7.5",
)
class GroupUserWhitelistPlugin(Star):
    """AstrBot 权限控制器主类。

    拦截策略：
    1. 群聊先检查黑名单，再检查管理员、群整体放行和用户-群号组合。
    2. 私聊只允许配置在 private_chat_users 中的普通用户；管理员可按配置绕过。
    3. allowed_groups 会同步到 AstrBot 平台白名单，避免核心层提前拦截群消息。
    """

    _log_filter_installed = False
    _log_filter = _AstrBotStopPropagationLogFilter()
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
        self._install_stop_propagation_log_filter()
        self._install_admin_wake_bypass_patch()
        self._install_private_whitelist_stage_patch()

    @classmethod
    def _install_stop_propagation_log_filter(cls):
        """安装日志过滤器，屏蔽指定 AstrBot 冗余日志。"""
        if cls._log_filter_installed:
            return
        target_loggers = [
            logging.getLogger(),
            logging.getLogger("astrbot"),
            logging.getLogger("Core"),
            logging.getLogger("core"),
            logging.getLogger("astrbot.core"),
            logging.getLogger("astrbot.core.pipeline.context_utils"),
            logging.getLogger("astrbot.core.pipeline.result_decorate.stage"),
            logging.getLogger("astrbot.core.pipeline.respond.stage"),
        ]
        for lg in target_loggers:
            try:
                lg.addFilter(cls._log_filter)
                for handler in getattr(lg, "handlers", []) or []:
                    handler.addFilter(cls._log_filter)
            except Exception:
                pass
        # 同时给当前已存在的所有 logger 和 handler 加过滤器，兼容 AstrBot 自定义 logger 名称。
        try:
            for lg in logging.Logger.manager.loggerDict.values():
                if isinstance(lg, logging.Logger):
                    lg.addFilter(cls._log_filter)
                    for handler in getattr(lg, "handlers", []) or []:
                        handler.addFilter(cls._log_filter)
        except Exception:
            pass
        cls._log_filter_installed = True

    def _cfg_get(self, key, default=None):
        """安全读取 AstrBotConfig/dict 配置，读取失败时返回默认值。"""
        try:
            if hasattr(self.config, "get"):
                return self.config.get(key, default)
        except Exception:
            pass
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
            users = data.get("private_chat_users", [])
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
                    value = data.get("admin_wake_bypass", False)
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

    def _sync_plugin_allowlist_to_platform_whitelist(self) -> None:
        """把插件放行对象同步到 AstrBot 平台 ID 白名单。

        AstrBot 核心平台白名单检查早于普通插件 handler。若只在本插件
        private_chat_users 中填写私聊 QQ，而没有同步到平台 id_whitelist，
        私聊消息会在插件私聊白名单逻辑执行前被核心白名单拦截。
        因此这里同时同步群聊放行群号和私聊白名单 QQ。
        """
        plugin_allowlist = self.allowed_groups | self.private_chat_users
        if not plugin_allowlist:
            return

        try:
            global_config = self.context.get_config()
        except Exception:
            global_config = None

        # 1. 尝试修改运行时配置对象。
        try:
            if hasattr(global_config, "get"):
                current = self._normalize_ids(global_config.get("id_whitelist", []))
                merged = sorted(current | plugin_allowlist)
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
            if not cmd_config_path.exists():
                return
            raw = cmd_config_path.read_text(encoding="utf-8-sig")
            data = json.loads(raw) if raw.strip() else {}
            platform_settings = data.setdefault("platform_settings", data)
            current = self._normalize_ids(platform_settings.get("id_whitelist", []))
            merged = sorted(current | plugin_allowlist)
            if merged != list(platform_settings.get("id_whitelist", [])):
                platform_settings["id_whitelist"] = merged
                cmd_config_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception as exc:
            logger.debug(f"同步插件放行列表到 cmd_config.json 失败: {exc}")

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

        if not self.private_chat_users:
            return

        # 不同适配器暴露的私聊 ID 字段不同，因此收集多个候选值做交集匹配。
        candidates = self._private_sender_candidates(event)
        if not candidates:
            return

        if self.admin_bypass and any(self._is_admin(item) for item in candidates):
            return

        if candidates & self.private_chat_users:
            return

        event.stop_event()
