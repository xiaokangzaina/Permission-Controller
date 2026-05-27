from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

try:
    from astrbot.api import logger
except Exception:  # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)

CONFIG_FILE = "astrbot_plugin_permission_controller_config.json"


class PermissionControllerPageService:
    """Web 面板配置读写服务。"""

    def __init__(self, plugin_dir: Path, config: Any):
        self.plugin_dir = Path(plugin_dir)
        self.config = config
        self.schema = self._load_schema(self.plugin_dir / "_conf_schema.json")

    async def get_bootstrap_payload(self) -> dict[str, Any]:
        return {"schema": self.schema, "config": self.get_config_snapshot()}

    def get_config_snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item_schema in self.schema.items():
            result[key] = self._cfg_get(key, copy.deepcopy(item_schema.get("default")))
        return result

    async def update_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("config must be an object")
        current = self.get_config_snapshot()
        updated: dict[str, Any] = {}
        for key, item_schema in self.schema.items():
            value = payload[key] if key in payload else current.get(key)
            updated[key] = self._sanitize_value(value, item_schema)
        self._save_config(updated)
        return self.get_config_snapshot()

    def _cfg_get(self, key: str, default: Any = None) -> Any:
        try:
            if hasattr(self.config, "get"):
                return self.config.get(key, default)
        except Exception:
            pass
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return default

    def _cfg_set(self, key: str, value: Any) -> None:
        if hasattr(self.config, "set"):
            self.config.set(key, value)
        elif isinstance(self.config, dict):
            self.config[key] = value
        else:
            try:
                setattr(self.config, key, value)
            except Exception:
                pass

    def _save_config(self, updated: dict[str, Any]) -> None:
        for key, value in updated.items():
            self._cfg_set(key, value)

        # AstrBot 不同版本传入的插件配置对象不完全一致。
        # 为避免 Web 保存时因 save_config 签名/实现差异抛出 500，
        # 这里固定写入插件配置文件，并尽量同步运行时配置对象。
        try:
            if hasattr(self.config, "save_config"):
                try:
                    self.config.save_config(updated)
                except TypeError:
                    self.config.save_config()
                return
        except Exception as exc:
            logger.warning("调用 AstrBot 配置保存接口失败，改为直接写入插件配置文件: %s", exc)

        self._write_config_file(updated)

    def _write_config_file(self, updated: dict[str, Any]) -> None:
        config_path = self.plugin_dir.parents[1] / "config" / CONFIG_FILE
        config_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if config_path.exists():
            try:
                raw = config_path.read_text(encoding="utf-8-sig")
                data = json.loads(raw) if raw.strip() else {}
            except Exception as exc:
                logger.warning("读取权限控制器配置失败，将重写配置: %s", exc)
        data.update(updated)
        config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _load_schema(schema_path: Path) -> dict[str, Any]:
        try:
            return json.loads(schema_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("加载权限控制器页面 schema 失败: %s", exc)
            return {}

    def _sanitize_value(self, value: Any, schema: dict[str, Any]) -> Any:
        field_type = schema.get("type", "string")
        if field_type == "bool":
            parsed = self._parse_bool(value)
            if parsed is None:
                raise ValueError(f"invalid bool value: {value}")
            return parsed
        if field_type == "list":
            return self._sanitize_list(value)
        if field_type == "int":
            return int(value)
        return "" if value is None else str(value)

    @staticmethod
    def _parse_bool(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"1", "true", "yes", "on", "开启", "开", "启用"}:
                return True
            if text in {"0", "false", "no", "off", "关闭", "关", "停用"}:
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        return None

    @staticmethod
    def _sanitize_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            items = []
            for line in value.replace(",", "\n").splitlines():
                line = line.strip()
                if line:
                    items.append(line)
            return list(dict.fromkeys(items))
        if isinstance(value, (list, tuple, set)):
            return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
        return [str(value).strip()] if str(value).strip() else []
