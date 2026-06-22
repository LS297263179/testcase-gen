"""认证相关路由 - 注册、登录、登出、用户信息"""

import json
import logging

from flask import Blueprint, jsonify, request, session

from core import config, db
from web.utils import check_rate_limit, generate_csrf_token, get_real_ip

logger = logging.getLogger("web")

bp = Blueprint("auth", __name__)


@bp.route("/api/register", methods=["POST"])
def api_register():
    """用户注册"""
    if not check_rate_limit(get_real_ip()):
        return jsonify({"error": "请求过于频繁，请稍后再试"}), 429
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "请求格式错误，请发送 JSON"}), 400
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400
    if len(username) < 2 or len(username) > 32:
        return jsonify({"error": "用户名长度需在 2-32 个字符之间"}), 400
    if not username.isascii() or not all(c.isalnum() or c in "-_" for c in username):
        return jsonify({"error": "用户名只能包含字母、数字、- 和 _"}), 400
    if len(password) < 8:
        return jsonify({"error": "密码长度至少 8 个字符"}), 400
    if len(password) > 128:
        return jsonify({"error": "密码长度不能超过 128 个字符"}), 400

    try:
        user_id = db.create_user(username, password)
        session["user_id"] = user_id
        session["username"] = username
        csrf_token = generate_csrf_token()
        return jsonify({"success": True, "user": {"id": user_id, "username": username}, "csrf_token": csrf_token})
    except ValueError as e:
        return jsonify({"error": str(e)}), 409


@bp.route("/api/login", methods=["POST"])
def api_login():
    """用户登录"""
    if not check_rate_limit(get_real_ip()):
        return jsonify({"error": "请求过于频繁，请稍后再试"}), 429
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "请求格式错误，请发送 JSON"}), 400
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400

    user = db.verify_user(username, password)
    if not user:
        return jsonify({"error": "用户名或密码错误"}), 401

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    csrf_token = generate_csrf_token()
    return jsonify({"success": True, "user": user, "csrf_token": csrf_token})


@bp.route("/api/logout", methods=["POST"])
def api_logout():
    """用户登出"""
    session.clear()
    return jsonify({"success": True})


@bp.route("/api/me")
def api_me():
    """获取当前登录用户信息"""
    if "user_id" in session:
        csrf_token = session.get("csrf_token") or generate_csrf_token()
        return jsonify({"logged_in": True, "user": {
            "id": session["user_id"],
            "username": session["username"],
        }, "csrf_token": csrf_token})
    return jsonify({"logged_in": False})
