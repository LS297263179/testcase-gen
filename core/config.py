"""集中配置管理模块

优先级：环境变量 > 数据库 settings > config.yaml > 默认值
所有模块通过此模块获取配置，不再各自读取 yaml 文件。
"""

import json
import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_config_cache: dict | None = None


# ============================================================
# 默认配置
# ============================================================

_DEFAULTS = {
    "secret_key": None,  # 必须由用户提供，否则随机生成
    "generate": {
        "api_type": "openai",
        "base_url": "",
        "api_key": "",
        "model": "",
        "image_model": "",
        "temperature": 0.3,
        "max_tokens": 4096,
        "max_retries": 3,
        "enable_thinking": False,
    },
    "review": {
        "enabled": False,
        "api_type": "openai",
        "base_url": "",
        "api_key": "",
        "model": "",
        "temperature": 0.3,
        "max_tokens": 4096,
        "max_retries": 3,
    },
    "output": {
        "dir": "./output",
        "format": "all",
    },
    "testcase": {
        "default_priority": "P1",
        "max_testcases": 100,
        "case_types": ["功能测试", "边界测试", "异常测试", "兼容性测试", "性能测试"],
    },
}


# ============================================================
# 文件配置加载
# ============================================================

def load_yaml_config(path: str | None = None) -> dict:
    """从 config.yaml 加载配置（仅用于 fallback）"""
    if path is None:
        path = str(_PROJECT_ROOT / "config.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cfg if isinstance(cfg, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"加载 config.yaml 失败: {e}")
        return {}


# ============================================================
# 模型配置（数据库优先，fallback 到文件）
# ============================================================

def get_model_config() -> dict:
    """获取模型配置（优先数据库，fallback 到 config.yaml）

    这是 web 层获取模型配置的统一入口。
    数据库中的 API Key 会自动解密。
    """
    from core import db

    raw = db.get_setting("model_config")
    if raw:
        try:
            config = json.loads(raw)
            # 解密 API Key
            for section in ("generate", "review"):
                if section in config and "api_key" in config[section]:
                    config[section]["api_key"] = db.decrypt_api_key(config[section]["api_key"])
            return config
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"数据库模型配置解析失败，fallback 到文件: {e}")

    # fallback 到 config.yaml
    file_cfg = load_yaml_config()
    return {
        "generate": file_cfg.get("generate", _DEFAULTS["generate"]),
        "review": file_cfg.get("review", _DEFAULTS["review"]),
    }


def get_testcase_config() -> dict:
    """获取用例生成配置（testcase 段）"""
    file_cfg = load_yaml_config()
    return file_cfg.get("testcase", _DEFAULTS["testcase"])


def get_output_config() -> dict:
    """获取输出配置"""
    file_cfg = load_yaml_config()
    return file_cfg.get("output", _DEFAULTS["output"])


# ============================================================
# Flask Secret Key
# ============================================================

def get_secret_key() -> bytes | str:
    """获取 Flask secret_key

    优先级：环境变量 FLASK_SECRET_KEY > config.yaml secret_key > 随机生成
    """
    # 1. 环境变量
    env_key = os.environ.get("FLASK_SECRET_KEY", "").strip()
    if env_key:
        return env_key

    # 2. config.yaml
    file_cfg = load_yaml_config()
    cfg_key = file_cfg.get("secret_key", "")
    if cfg_key and cfg_key not in ("", "your-secret-key-here", "CHANGE_ME"):
        return cfg_key

    # 3. 随机生成（重启后失效，但不会硬编码）
    logger.warning(
        "未配置 FLASK_SECRET_KEY 或 config.yaml secret_key，"
        "使用随机密钥（重启后 session 将失效）。"
        "建议在 .env 或环境变量中设置 FLASK_SECRET_KEY。"
    )
    return os.urandom(24)
