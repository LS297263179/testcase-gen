"""Flask Web 应用包 - 路由注册 + 全局配置"""

import logging
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session

from core import config, db
from core.v2.bootstrap import ensure_v2_ready
from web.utils import generate_csrf_token, get_real_ip

# 统一日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("web")

_PROJECT_ROOT = Path(__file__).parent.parent

app = Flask(
    __name__,
    template_folder=str(_PROJECT_ROOT / "templates"),
    static_folder=str(_PROJECT_ROOT / "static"),
)
app.secret_key = config.get_secret_key()

# 初始化数据库
db.init_db()
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32MB（支持多张图片）


# ============================================================
# V2 初始化（失败隔离，保护 V1；仅留状态，API 行为全部留 10.3）
# ============================================================


def _bootstrap_v2(flask_app) -> bool:
    """初始化 V2 并隔离故障（V1 不受影响）。

    bootstrap 负责正确初始化、失败即 raise；本函数负责 try/except 隔离，保护 V1。
    返回 v2_ready 状态并写入 flask_app.config["V2_READY"]，供 10.3 决定 /api/v2/* 行为。
    ★ 10.1 只留状态：不注册 V2 blueprint、不加任何 /api/v2/* 路由或 503 handler（全部留 10.3）。
    """
    try:
        ensure_v2_ready()
        flask_app.config["V2_READY"] = True
        logger.info("V2 初始化成功，V2 功能已启用")
        return True
    except Exception:
        flask_app.config["V2_READY"] = False
        logger.exception("V2 初始化失败，V2 功能已隔离，V1 不受影响（/api/v2/* 将在 10.3 统一返回 503）")
        return False


V2_READY = _bootstrap_v2(app)


# ============================================================
# 注册 Blueprint
# ============================================================

from web.auth import bp as auth_bp
from web.config_routes import bp as config_bp
from web.data import bp as data_bp
from web.generate import bp as generate_bp
from web.v2_routes import bp as v2_bp

app.register_blueprint(auth_bp)
app.register_blueprint(config_bp)
app.register_blueprint(data_bp)
app.register_blueprint(generate_bp)
app.register_blueprint(v2_bp)  # Step 10.3：V2 REST API（/api/v2/*，V2_READY gating 见 v2_routes.before_request）


# ============================================================
# 免登录模式（公共实例）
# ============================================================


@app.before_request
def _guest_auto_login():
    """GUEST_AUTO_LOGIN=1 时，无 session 的请求自动落到共享访客账号。

    只补 session：48 处 login_required、csrf_protect 与 V2 的 session→user 映射均按原样生效；
    前端 #authPage/#appPage 默认都是 display:none，checkAuth() 拿到 logged_in 直接进主界面。
    """
    if not config.guest_mode_enabled() or "user_id" in session:
        return
    if request.path.startswith(("/static/", "/api/health")):
        return
    user = db.get_or_create_user(config.guest_username())
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["guest"] = True  # 配置锁按此判定，正式账号登录后不受限
    if not session.get("csrf_token"):
        generate_csrf_token()


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
    return e.get_response() if hasattr(e, "get_response") else ("Not Found", 404)


@app.errorhandler(500)
def internal_error(e):
    """500 统一返回 JSON"""
    if request.path.startswith("/api/"):
        return jsonify({"error": "服务器内部错误"}), 500
    return e.get_response() if hasattr(e, "get_response") else ("Internal Server Error", 500)


@app.errorhandler(413)
def too_large(e):
    """文件过大"""
    return jsonify({"error": "上传文件过大，最大支持 32MB"}), 413


# ============================================================
# 页面路由 + 健康检查
# ============================================================


@app.route("/")
def index():
    """默认入口给 V2；未登录时仍回 V1，因为登录/注册界面只在 V1 里。

    不能无条件跳 /v2：/v2 对未登录者 302 回 /，两者会成环。
    """
    if "user_id" in session:
        return redirect("/v2")
    return render_template("index.html", guest_mode=config.guest_mode_enabled())


@app.route("/v1")
def v1_index():
    """V1 工作台（原 `/`）。"""
    return render_template("index.html", guest_mode=config.guest_mode_enabled())


@app.route("/v2")
def v2_index():
    """V2 独立页面（Step 10.4）。决策2：不重造登录——未登录跳回 V1 `/` 登录。

    认证层=V1（session），业务数据=V2；衔接 10.3 的 V1 session→V2 user 自动映射。
    V1 index.html/app.js/style.css 一律不改。
    """
    if "user_id" not in session:
        return redirect("/")
    return render_template("v2.html")


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
