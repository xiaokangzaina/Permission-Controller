import json
import logging
from pathlib import Path
from sys import maxsize

logger = logging.getLogger(__name__)

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register


class _AstrBotStopPropagationLogFilter(logging.Filter):
    """屏蔽 AstrBot 框架输出的指定“终止事件传播”调试日志。"""

    TARGET_TEXT = "astrbot - after_message_sent 终止了事件传播。"

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return self.TARGET_TEXT not in str(msg)


@register(
    "astrbot_plugin_group_user_whitelist",
    "local",
    "按 用户QQ-群号/群号列表 限制谁能调用模型/机器人",
    "1.5.0",
)
class GroupUserWhitelistPlugin(Star):
    """群内用户级白名单。"""

    _log_filter_installed = False
    _log_filter = _AstrBotStopPropagationLogFilter()

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.context = context
        self.config = config or {}
        self.rules = self._load_rules()
        self.admin_bypass = self._get_bool_config("admin_bypass", True)
        self.enable_group_rules = self._get_bool_config("enable_group_rules", True)
        self.enable_group_blacklist = self._get_bool_config(
            "enable_group_blacklist", True
        )
        self.admin_ids = self._load_admin_ids()
        self.group_blacklist = self._normalize_ids(
            self._cfg_get("group_blacklist", [])
        )
        self.private_chat_users = self._normalize_ids(
            self._cfg_get("private_chat_users", [])
        )
        self.allowed_groups = self._normalize_ids(
            self._cfg_get("allowed_groups", [])
        )
        self._sync_allowed_groups_to_platform_whitelist()
        self._install_stop_propagation_log_filter()

    @classmethod
    def _install_stop_propagation_log_filter(cls):
        """安装日志过滤器，屏蔽：astrbot - after_message_sent 终止了事件传播。"""
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
        try:
            if hasattr(self.config, "get"):
                return self.config.get(key, default)
        except Exception:
            pass
        return default

    def _get_bool_config(self, key, default=False):
        value = self._cfg_get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in (
                "1", "true", "yes", "on", "开启", "开", "启用"
            )
        return bool(value)

    @staticmethod
    def _normalize_ids(value):
        if value is None:
            return set()
        if isinstance(value, (str, int)):
            value = [value]
        if not isinstance(value, list):
            return set()
        return {str(item).strip() for item in value if str(item).strip()}

    def _load_admin_ids(self):
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

    def _sync_allowed_groups_to_platform_whitelist(self) -> None:
        """把插件群聊放行列表同步到 AstrBot 平台 ID 白名单。

        目的：即使用户没有在 AstrBot 普通配置 -> 平台配置 -> 白名单 ID 列表
        手动填写群号，只要在本插件的“放行权限QQ群聊列表”中填写群号，
        平台核心白名单也能放行这些群聊消息，让后续插件规则有机会执行。
        """
        if not self.allowed_groups:
            return

        try:
            global_config = self.context.get_config()
        except Exception:
            global_config = None

        # 1. 尝试修改运行时配置对象。
        try:
            if hasattr(global_config, "get"):
                current = self._normalize_ids(global_config.get("id_whitelist", []))
                merged = sorted(current | self.allowed_groups)
                if hasattr(global_config, "set"):
                    global_config.set("id_whitelist", merged)
                elif isinstance(global_config, dict):
                    global_config["id_whitelist"] = merged
        except Exception as exc:
            logger.debug(f"同步群聊放行列表到运行时平台白名单失败: {exc}")

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
            merged = sorted(current | self.allowed_groups)
            if merged != list(platform_settings.get("id_whitelist", [])):
                platform_settings["id_whitelist"] = merged
                cmd_config_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception as exc:
            logger.debug(f"同步群聊放行列表到 cmd_config.json 失败: {exc}")

    def _load_rules(self):
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
        candidates = set()
        getters = [
            getattr(event, "get_sender_id", None),
            getattr(event, "get_session_id", None),
        ]
        for getter in getters:
            if callable(getter):
                try:
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
            candidates.update(
                self._extract_tail_ids_from_unified_origin(event.unified_msg_origin)
            )
        except Exception:
            pass
        return {x for x in candidates if x}

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=maxsize)
    async def check_group_user_whitelist(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id() or "").strip()
        sender_id = str(event.get_sender_id() or "").strip()

        if self.enable_group_blacklist and sender_id in self.group_blacklist:
            if self.admin_bypass and self._is_admin(sender_id):
                return
            event.stop_event()
            return

        if not self.enable_group_rules:
            return

        if group_id in self.allowed_groups:
            return

        allowed_users = self.rules.get(group_id)
        if not allowed_users:
            return
        if self.admin_bypass and self._is_admin(sender_id):
            return
        if sender_id in allowed_users:
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

        candidates = self._private_sender_candidates(event)
        if not candidates:
            return

        if self.admin_bypass and any(self._is_admin(item) for item in candidates):
            return

        if candidates & self.private_chat_users:
            return

        event.stop_event()
