"""Flask Web 应用 - 路由注册 + 全局配置"""

import logging

from flask import Flask, jsonify, render_template, request, session

import config
import db
from web_utils import get_real_ip

# 统一日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("web")

app = Flask(__name__)
app.secret_key = config.get_secret_key()

# 初始化数据库
db.init_db()
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32MB（支持多张图片）


# ============================================================
# 注册 Blueprint
# ============================================================

from web_auth import bp as auth_bp
from web_config import bp as config_bp
from web_data import bp as data_bp
from web_generate import bp as generate_bp

app.register_blueprint(auth_bp)
app.register_blueprint(config_bp)
app.register_blueprint(data_bp)
app.register_blueprint(generate_bp)


# ============================================================
# 全局请求日志
# ============================================================

@app.before_request
def _log_request():
    """记录请求日志"""
    if request.path.startswith("/api/") and request.path != "/api/health":
        logger.info(f"{request.method} {request.path} user={session.get('username', '-')} ip={get_real_ip()}")


# ============================================================
# 全局错误处理
# ============================================================

@app.errorhandler(404)
def not_found(e):
    """404 统一返回 JSON"""
    if request.path.startswith("/api/"):
        return jsonify({"error": "接口不存在"}), 404
    return e.get_response() if hasattr(e, 'get_response') else ("Not Found", 404)


@app.errorhandler(500)
def internal_error(e):
    """500 统一返回 JSON"""
    if request.path.startswith("/api/"):
        return jsonify({"error": "服务器内部错误"}), 500
    return e.get_response() if hasattr(e, 'get_response') else ("Internal Server Error", 500)


@app.errorhandler(413)
def too_large(e):
    """文件过大"""
    return jsonify({"error": "上传文件过大，最大支持 32MB"}), 413


# ============================================================
# 页面路由 + 健康检查
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def api_health():
    """健康检查端点（检查数据库连通性）"""
    checks = {"status": "ok"}
    try:
        with db.db_read_conn() as conn:
            conn.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:
        checks["status"] = "degraded"
        checks["database"] = f"error: {e}"
    return jsonify(checks), 200 if checks["status"] == "ok" else 503
