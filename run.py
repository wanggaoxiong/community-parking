#!/usr/bin/env python3
import argparse
import logging
import signal
import socket
import threading
from typing import Optional

from app.config import config_manager
from app.ftp_receiver import BackgroundFTPServer
from app.server import (
    app_ctx,
    create_http_server,
    create_mobile_http_server,
    create_voice_http_server,
)


def _detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return "127.0.0.1"


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    cfg = config_manager.get()
    parser = argparse.ArgumentParser(description="小区公益智能寻位系统服务端")
    parser.add_argument("--host", default="127.0.0.1", help="主控管理端 HTTP 监听地址")
    parser.add_argument("--port", type=int, default=int(cfg["http_port"]), help="主控管理端 HTTP 端口")
    parser.add_argument(
        "--mobile-host",
        default=str(cfg.get("mobile_http_host", "0.0.0.0")),
        help="业主手机查看端 HTTP 监听地址（默认 0.0.0.0 支持局域网手机直连）",
    )
    parser.add_argument(
        "--mobile-port",
        type=int,
        default=int(cfg.get("mobile_http_port", 18790)),
        help="业主手机查看端只读 HTTP 端口（默认 18790，设为 0 可禁用）",
    )
    parser.add_argument("--no-mobile", action="store_true", help="禁用业主手机查看端端口")
    parser.add_argument(
        "--voice-host",
        default=str(cfg.get("voice_http_host", "0.0.0.0")),
        help="免手语音/Siri快捷指令/大模型问答网关监听地址（默认 0.0.0.0）",
    )
    parser.add_argument(
        "--voice-port",
        type=int,
        default=int(cfg.get("voice_http_port", 18791)),
        help="免手语音/Siri快捷指令/大模型问答网关端口（默认 18791，设为 0 可禁用）",
    )
    parser.add_argument("--no-voice", action="store_true", help="禁用 18791 免手语音与问答网关端口")
    parser.add_argument("--ftp-port", type=int, default=int(cfg["ftp_port"]), help="内存 FTP 抓图接收端口")
    parser.add_argument("--no-ftp", action="store_true", help="禁用内嵌内存 FTP 服务")
    args = parser.parse_args()

    if not args.no_ftp:
        ftp = BackgroundFTPServer(on_snapshot=app_ctx.handle_ftp_snapshot, port=args.ftp_port)
        ftp.start()

    httpd = create_http_server(host=args.host, port=args.port, ctx=app_ctx)
    mobile_httpd: Optional[object] = None
    if not args.no_mobile and args.mobile_port > 0:
        mobile_httpd = create_mobile_http_server(host=args.mobile_host, port=args.mobile_port, ctx=app_ctx)
        threading.Thread(
            target=mobile_httpd.serve_forever,
            name="MobileViewerHTTPServer",
            daemon=True,
        ).start()

    voice_httpd: Optional[object] = None
    vg_enabled = bool(cfg.get("voice_gateway", {}).get("enabled", True))
    if not args.no_voice and args.voice_port > 0 and vg_enabled:
        app_ctx.voice_gateway.start_background_workers()
        voice_httpd = create_voice_http_server(host=args.voice_host, port=args.voice_port, ctx=app_ctx)
        threading.Thread(
            target=voice_httpd.serve_forever,
            name="VoiceGatewayHTTPServer",
            daemon=True,
        ).start()

    lan_ip = _detect_lan_ip()
    print(f"🚗 主控管理端已启动: http://{args.host}:{args.port}", flush=True)
    if mobile_httpd is not None:
        local_mob_host = "127.0.0.1" if args.mobile_host in ("0.0.0.0", "::") else args.mobile_host
        lan_hint = (
            f" | 局域网手机访问: http://{lan_ip}:{args.mobile_port}"
            if args.mobile_host in ("0.0.0.0", "::") and lan_ip != "127.0.0.1"
            else ""
        )
        print(
            f"📱 业主手机端已启动: http://{local_mob_host}:{args.mobile_port}{lan_hint}",
            flush=True,
        )
    if voice_httpd is not None:
        local_voice_host = "127.0.0.1" if args.voice_host in ("0.0.0.0", "::") else args.voice_host
        voice_lan_hint = (
            f" | 局域网 Siri/语音网关: http://{lan_ip}:{args.voice_port}"
            if args.voice_host in ("0.0.0.0", "::") and lan_ip != "127.0.0.1"
            else ""
        )
        print(
            f"🎙️ 免手语音/Siri/大模型网关已启动: http://{local_voice_host}:{args.voice_port}{voice_lan_hint}",
            flush=True,
        )
    if not args.no_ftp:
        print(
            f"📷 零落盘内存 FTP 抓图接收服务: ftp://{args.host}:{args.ftp_port} (账号: {cfg['ftp_user']})",
            flush=True,
        )

    stopping = threading.Event()

    def _request_shutdown(signum, _frame) -> None:
        if stopping.is_set():
            return
        stopping.set()
        print(f"\n收到退出信号 ({signum})，正在安全刷写 SQLite WAL 并停止服务...", flush=True)
        threading.Thread(target=httpd.shutdown, name="GracefulShutdown", daemon=True).start()

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭服务...", flush=True)
    finally:
        if voice_httpd is not None:
            try:
                voice_httpd.shutdown()
                voice_httpd.server_close()
            except Exception:
                pass
        if mobile_httpd is not None:
            try:
                mobile_httpd.shutdown()
                mobile_httpd.server_close()
            except Exception:
                pass
        app_ctx.voice_gateway.stop_background_workers()
        try:
            app_ctx.ensure_workspace_registry().stop_all_and_checkpoint()
        except Exception:
            pass
        try:
            app_ctx.db.checkpoint_wal()
        except Exception:
            pass
        httpd.server_close()


if __name__ == "__main__":
    main()
