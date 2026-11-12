#!/usr/bin/env python3
import argparse
import logging

from app.config import config_manager
from app.ftp_receiver import BackgroundFTPServer
from app.server import app_ctx, create_http_server


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    cfg = config_manager.get()
    parser = argparse.ArgumentParser(description="小区公益智能寻位系统服务端")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP 监听地址")
    parser.add_argument("--port", type=int, default=int(cfg["http_port"]), help="HTTP 端口")
    parser.add_argument("--ftp-port", type=int, default=int(cfg["ftp_port"]), help="内存 FTP 抓图接收端口")
    parser.add_argument("--no-ftp", action="store_true", help="禁用内嵌内存 FTP 服务")
    args = parser.parse_args()

    if not args.no_ftp:
        ftp = BackgroundFTPServer(on_snapshot=app_ctx.handle_ftp_snapshot, port=args.ftp_port)
        ftp.start()

    httpd = create_http_server(host=args.host, port=args.port, ctx=app_ctx)
    print(f"🚗 小区公益智能寻位系统已启动: http://{args.host}:{args.port}")
    if not args.no_ftp:
        print(f"📷 零落盘内存 FTP 抓图接收服务: ftp://{args.host}:{args.ftp_port} (账号: {cfg['ftp_user']})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭服务...")
        httpd.server_close()


if __name__ == "__main__":
    main()
