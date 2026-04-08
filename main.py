from __future__ import annotations

import argparse
import signal
from pathlib import Path

from uptime_robot.config import load_config
from uptime_robot.monitor import MonitorService
from uptime_robot.storage import Storage
from uptime_robot.web import make_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dependency-free uptime monitor for DevOps workloads")
    parser.add_argument("--config", default="monitors.json", help="Path to monitor config JSON")
    parser.add_argument("--host", default="0.0.0.0", help="Dashboard listen host")
    parser.add_argument("--port", type=int, default=8000, help="Dashboard listen port")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    storage = Storage(config.data_dir)
    service = MonitorService(config, storage)
    server = make_server(args.host, args.port, storage)

    def shutdown(*_: object) -> None:
        service.stop()
        server.shutdown()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    service.start()
    print(f"Uptime Robot running on http://{args.host}:{args.port} using config {Path(args.config).resolve()}")
    server.serve_forever()


if __name__ == "__main__":
    main()
