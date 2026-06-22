"""模型配置路由 - 预设模型、配置读写、仪表盘"""

import json
import logging

from flask import Blueprint, jsonify, request, session

from core import config, db
from web.utils import csrf_protect, login_required

logger = logging.getLogger("web")

bp = Blueprint("config", __name__)

# 预设模型（provider 字段用于判断 Key 是否可复用）
MODEL_PRESETS = {
    "mimo": {
        "name": "MiMo",
        "provider": "mimo",
        "generate": {"api_type": "openai", "base_url": "https://token-plan-cn.xiaomimimo.com/v1", "model": "mimo-v2.5", "image_model": "mimo-v2.5", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enable_thinking": True},
        "review":    {"api_type": "openai", "base_url": "https://token-plan-cn.xiaomimimo.com/v1", "model": "mimo-v2.5", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enabled": True},
    },
    "dashscope-deepseek": {
        "name": "DeepSeek (阿里云)",
        "provider": "dashscope",
        "generate": {"api_type": "openai", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "deepseek-v4-pro", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enable_thinking": False},
        "review":    {"api_type": "openai", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "deepseek-v4-pro", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enabled": True},
    },
    "qwen": {
        "name": "通义千问 (阿里云)",
        "provider": "dashscope",
        "generate": {"api_type": "openai", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-max", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enable_thinking": False},
        "review":    {"api_type": "openai", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-max", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enabled": True},
    },
    "kimi": {
        "name": "Kimi (月之暗面)",
        "provider": "moonshot",
        "generate": {"api_type": "openai", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enable_thinking": False},
        "review":    {"api_type": "openai", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enabled": True},
    },
    "openai": {
        "name": "OpenAI GPT",
        "provider": "openai",
        "generate": {"api_type": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enable_thinking": False},
        "review":    {"api_type": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o", "temperature": 0.3, "max_tokens": 4096, "max_retries": 3, "enabled": True},
    },
}


@bp.route("/api/model-presets")
@login_required
def api_model_presets():
    """返回预设模型列表"""
    presets = {k: {"name": v["name"]} for k, v in MODEL_PRESETS.items()}
    return jsonify({"presets": presets})


@bp.route("/api/model-config", methods=["GET"])
@login_required
def api_model_config_get():
    """获取当前模型配置"""
    cfg = config.get_model_config()
    for section in ("generate", "review"):
        if section in cfg and "api_key" in cfg[section]:
            key = cfg[section]["api_key"]
            if key and len(key) > 8:
                cfg[section]["api_key_hint"] = key[:4] + "****" + key[-4:]
            else:
                cfg[section]["api_key_hint"] = "****"
    return jsonify({"config": cfg, "presets": {k: v["name"] for k, v in MODEL_PRESETS.items()}})


@bp.route("/api/model-config", methods=["POST"])
@login_required
@csrf_protect
def api_model_config_set():
    """保存模型配置"""
    data = request.get_json()
    preset = data.get("preset")
    need_key = False

    if preset and preset in MODEL_PRESETS:
        preset_data = MODEL_PRESETS[preset]
        cfg = {
            "generate": {**preset_data["generate"]},
            "review": {**preset_data["review"]},
        }
        new_provider = preset_data.get("provider", "")
        stored_keys = db.get_setting("provider_keys")
        stored_keys = json.loads(stored_keys) if stored_keys else {}

        found_key = stored_keys.get(new_provider, "")

        if not found_key:
            old = config.get_model_config()
            if old.get("_provider") == new_provider:
                found_key = old.get("generate", {}).get("api_key", "")

        need_key = not found_key

        if found_key:
            for section in ("generate", "review"):
                cfg[section]["api_key"] = found_key

        cfg["_provider"] = new_provider
    else:
        cfg = data.get("config", {})
        old = config.get_model_config()
        for section in ("generate", "review"):
            if section in cfg:
                key = cfg[section].get("api_key", "")
                if not key or "****" in key:
                    old_key = old.get(section, {}).get("api_key", "")
                    cfg[section]["api_key"] = old_key

    db.save_model_config(cfg)

    gen_key = cfg.get("generate", {}).get("api_key", "")
    prov = cfg.get("_provider", "")
    if gen_key and "****" not in gen_key and prov:
        stored = json.loads(db.get_setting("provider_keys") or "{}")
        stored[prov] = gen_key
        db.set_setting("provider_keys", json.dumps(stored))

    return jsonify({"success": True, "need_key": need_key if preset and preset in MODEL_PRESETS else False})


@bp.route("/api/dashboard")
@login_required
def api_dashboard():
    """仪表盘统计"""
    user_id = session["user_id"]
    stats = db.get_dashboard_stats(user_id)
    prefs = db.list_all_preferences(user_id=user_id)
    stats["preference_count"] = len(prefs)
    cfg = config.get_model_config()
    stats["current_model"] = cfg.get("generate", {}).get("model", "-")
    return jsonify(stats)
