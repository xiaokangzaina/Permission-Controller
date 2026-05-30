from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from astrbot.api import logger

PLUGIN_DIR = Path(__file__).resolve().parent

# schema 顶层分组键 -> 其 items
# 配置在 _conf_schema.json 中是两层结构：
#   private_chat_settings.items.{...}
#   group_chat_settings.items.{...}
# 前端按分组渲染，后端按 key 写回到对应分组。


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on", "开启", "开", "启用"):
            return True
        if text in ("0", "false", "no", "off", "关闭", "关", "禁用"):
            return False
    return None


def _normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[\n,，]+", value)
    elif isinstance(value, list):
        parts = value
    else:
        return []
    return [str(item).strip() for item in parts if str(item).strip()]


class PermissionPageService:
    """权限控制器配置页服务。

    与 qqadmin 的 page_service 角色一致，但 permission_controller 是
    全局单页配置（没有"按群"维度），因此只提供 bootstrap / save / reset 三类操作。
    """

    def __init__(self, plugin: Any):
        # plugin 为 GroupUserWhitelistPlugin 实例，持有 self.config (AstrBotConfig)
        self.plugin = plugin
        self.schema = self._load_schema(PLUGIN_DIR / "_conf_schema.json")

    # ---------- 对外接口 ----------

    def get_bootstrap_payload(self) -> dict[str, Any]:
        """返回 schema 分组结构 + 当前配置值。"""
        config = self._read_current_config()
        return {
            "schema": self.schema,
            "config": config,
            "groups": self._build_configured_groups(config),
        }

    async def list_groups(self, force: bool = False) -> list[dict[str, Any]]:
        """返回机器人已加入的 QQ 群列表；失败时回退到配置中的群号。"""
        groups: dict[str, dict[str, Any]] = {}
        for client in self._iter_qq_clients():
            try:
                result = await client.call_action("get_group_list")
                for item in self._extract_group_list(result):
                    group_id = str(item.get("group_id", "")).strip()
                    if not group_id or group_id in groups:
                        continue
                    groups[group_id] = self._normalize_group_item(item)
            except Exception as exc:
                logger.debug("[PermissionController] 获取群列表失败: %s", exc)
        for item in self._build_configured_groups(self._read_current_config()):
            groups.setdefault(item["group_id"], item)
        return sorted(groups.values(), key=lambda item: item.get("group_name") or item["group_id"])

    def get_group_config(self, group_id: str) -> dict[str, Any]:
        """把全局配置映射成单群配置页需要的数据。"""
        group_id = str(group_id or "").strip()
        if not group_id:
            raise ValueError("group_id must not be empty")
        config = self._read_current_config()
        users = []
        for rule in _normalize_list(config.get("simple_rules")):
            if "-" not in rule:
                continue
            user_id, target_group_id = rule.split("-", 1)
            if target_group_id.strip() == group_id and user_id.strip():
                users.append(user_id.strip())
        return {
            "group_info": self._build_group_info(group_id),
            "config": {
                "group_enabled": group_id in set(_normalize_list(config.get("allowed_groups"))),
                "allowed_users": sorted(set(users)),
            },
        }

    def update_group_config(self, group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """保存单群配置到 allowed_groups/simple_rules。"""
        group_id = str(group_id or "").strip()
        if not group_id:
            raise ValueError("group_id must not be empty")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")

        config = self._read_current_config()
        allowed_groups = set(_normalize_list(config.get("allowed_groups")))
        simple_rules = []
        for rule in _normalize_list(config.get("simple_rules")):
            if "-" not in rule:
                simple_rules.append(rule)
                continue
            user_id, target_group_id = rule.split("-", 1)
            if target_group_id.strip() != group_id:
                simple_rules.append(f"{user_id.strip()}-{target_group_id.strip()}")

        if _parse_bool(payload.get("group_enabled")):
            allowed_groups.add(group_id)
        else:
            allowed_groups.discard(group_id)

        for user_id in _normalize_list(payload.get("allowed_users")):
            if user_id.isdigit():
                simple_rules.append(f"{user_id}-{group_id}")

        self._write_config({
            "allowed_groups": sorted(allowed_groups),
            "simple_rules": sorted(set(simple_rules)),
        })
        return self.get_group_config(group_id)

    def reset_group_config(self, group_id: str) -> dict[str, Any]:
        """清空单群放行和该群用户规则。"""
        return self.update_group_config(group_id, {"group_enabled": False, "allowed_users": []})

    def update_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """根据前端提交的扁平 key->value，按 schema 清洗并写回配置文件。"""
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")

        flat_schema = self._flatten_schema_items()
        sanitized: dict[str, Any] = {}
        for key, field_schema in flat_schema.items():
            if key not in payload:
                continue
            sanitized[key] = self._sanitize_value(payload[key], field_schema)

        if not sanitized:
            raise ValueError("no valid config fields to update")

        self._write_config(sanitized)
        return self._read_current_config()

    def reset_config(self) -> dict[str, Any]:
        """将所有字段恢复为 schema 默认值并写回。"""
        flat_schema = self._flatten_schema_items()
        defaults = {
            key: field.get("default") for key, field in flat_schema.items()
        }
        self._write_config(defaults)
        return self._read_current_config()

    # ---------- 群列表辅助 ----------

    def _iter_qq_clients(self) -> list[Any]:
        clients: list[Any] = []
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
                AiocqhttpAdapter,
            )
        except Exception:
            AiocqhttpAdapter = None
        try:
            platform_insts = self.plugin.context.platform_manager.platform_insts
        except Exception:
            platform_insts = []
        for inst in platform_insts:
            if AiocqhttpAdapter is not None and not isinstance(inst, AiocqhttpAdapter):
                continue
            try:
                client = inst.get_client()
            except Exception:
                continue
            if client is not None:
                clients.append(client)
        return clients

    @staticmethod
    def _extract_group_list(result: Any) -> list[dict[str, Any]]:
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        return []

    def _build_configured_groups(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        group_ids = set(_normalize_list(config.get("allowed_groups")))
        for rule in _normalize_list(config.get("simple_rules")):
            if "-" not in rule:
                continue
            _, group_id = rule.split("-", 1)
            group_id = group_id.strip()
            if group_id:
                group_ids.add(group_id)
        return [self._build_group_info(group_id, source="configured") for group_id in sorted(group_ids)]

    def _build_group_info(self, group_id: str, source: str = "fallback") -> dict[str, Any]:
        group_id = str(group_id).strip()
        return {
            "group_id": group_id,
            "group_name": f"群 {group_id}",
            "avatar": f"https://p.qlogo.cn/gh/{group_id}/{group_id}/640",
            "member_count": 0,
            "max_member_count": 0,
            "source": source,
        }

    def _normalize_group_item(self, item: dict[str, Any]) -> dict[str, Any]:
        group_id = str(item.get("group_id", "")).strip()
        group_name = str(item.get("group_name", "")).strip() or f"群 {group_id}"
        normalized = self._build_group_info(group_id, source="live")
        normalized.update(
            {
                "group_name": group_name,
                "member_count": self._safe_int(item.get("member_count"), 0),
                "max_member_count": self._safe_int(item.get("max_member_count"), 0),
            }
        )
        return normalized

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    # ---------- 配置读写 ----------

    def _read_current_config(self) -> dict[str, Any]:
        """读取当前全部字段的值（扁平 key->value）。"""
        result: dict[str, Any] = {}
        for key, field in self._flatten_schema_items().items():
            value = self._cfg_get(key, field.get("default"))
            if field.get("type") == "list":
                value = _normalize_list(value)
            elif field.get("type") == "bool":
                parsed = _parse_bool(value)
                value = field.get("default", False) if parsed is None else parsed
            result[key] = value
        return result

    def _cfg_get(self, key: str, default: Any = None) -> Any:
        """复用插件的兼容读取逻辑（支持分组嵌套）。"""
        getter = getattr(self.plugin, "_cfg_get", None)
        if callable(getter):
            try:
                return getter(key, default)
            except Exception:
                pass
        config = getattr(self.plugin, "config", {})
        try:
            if hasattr(config, "get"):
                value = config.get(key, None)
                if value is not None:
                    return value
        except Exception:
            pass
        return default

    def _write_config(self, sanitized: dict[str, Any]) -> None:
        """把清洗后的值写回 AstrBotConfig，并持久化 + 刷新运行时缓存。"""
        config = getattr(self.plugin, "config", None)
        if config is None:
            raise RuntimeError("plugin config is unavailable")

        for key, value in sanitized.items():
            group = self._group_for_key(key)
            self._set_in_config(config, group, key, value)

        # 持久化
        save = getattr(config, "save_config", None)
        if callable(save):
            save()
        else:
            logger.warning("[PermissionController] config 不支持 save_config，跳过持久化")

        # 通知插件重载运行时缓存
        reload_fn = getattr(self.plugin, "reload_runtime_config", None)
        if callable(reload_fn):
            try:
                reload_fn()
            except Exception as exc:
                logger.warning("[PermissionController] 运行时配置重载失败: %s", exc)

    @staticmethod
    def _set_in_config(config: Any, group: str | None, key: str, value: Any) -> None:
        """优先写入分组（新版结构），否则写入顶层（兼容旧版平铺）。"""
        try:
            if group is not None and hasattr(config, "get"):
                sub = config.get(group, None)
                if isinstance(sub, dict):
                    sub[key] = value
                    config[group] = sub
                    return
        except Exception:
            pass
        try:
            config[key] = value
        except Exception:
            if hasattr(config, "__setitem__"):
                config[key] = value

    # ---------- schema 处理 ----------

    @staticmethod
    def _load_schema(schema_path: Path) -> dict[str, Any]:
        try:
            return json.loads(schema_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[PermissionController] 加载 schema 失败: %s", exc)
            return {}

    def _flatten_schema_items(self) -> dict[str, Any]:
        """把两层 schema 摊平成 key->field_schema。"""
        flat: dict[str, Any] = {}
        for group_def in self.schema.values():
            items = (group_def or {}).get("items", {})
            if isinstance(items, dict):
                for key, field in items.items():
                    flat[key] = field
        return flat

    def _group_for_key(self, key: str) -> str | None:
        """找出某字段属于哪个顶层分组。"""
        for group_name, group_def in self.schema.items():
            items = (group_def or {}).get("items", {})
            if isinstance(items, dict) and key in items:
                return group_name
        return None

    def _sanitize_value(self, value: Any, field_schema: dict[str, Any]) -> Any:
        field_type = field_schema.get("type", "string")
        if field_type == "bool":
            parsed = _parse_bool(value)
            if parsed is None:
                raise ValueError(f"invalid bool value: {value!r}")
            return parsed
        if field_type == "list":
            return _normalize_list(value)
        if field_type == "int":
            try:
                return int(value)
            except (TypeError, ValueError):
                raise ValueError(f"invalid int value: {value!r}")
        return str(value if value is not None else field_schema.get("default", ""))
