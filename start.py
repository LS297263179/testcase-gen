"""启动器 - 启动 Web 服务"""

import argparse
import webbrowser
import threading

from web import app


def main():
    parser = argparse.ArgumentParser(description="AI 测试用例生成器 - Web 版")
    parser.add_argument("-p", "--port", type=int, default=5000, help="端口号 (默认: 5000)")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认: 0.0.0.0)")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--debug", action="store_true", help="启用调试模式（仅开发环境）")
    args = parser.parse_args()

    url = f"http://localhost:{args.port}"
    print(f"\n  AI 测试用例生成器")
    print(f"  访问地址: {url}")
    print(f"  按 Ctrl+C 停止服务\n")

    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()

