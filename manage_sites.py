from __future__ import annotations

import argparse
import sys
from pathlib import Path

from uptime_robot.common import SiteConfig, load_settings, load_sites, parse_site, save_sites


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage uptime monitor sites.json entries")
    parser.add_argument("--sites-file", default=None, help="Override the sites.json path from .env")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List configured sites")

    add_parser = subparsers.add_parser("add", help="Add a new site")
    _add_site_arguments(add_parser)

    remove_parser = subparsers.add_parser("remove", help="Remove a site by name")
    remove_parser.add_argument("--name", required=True)

    enable_parser = subparsers.add_parser("enable", help="Enable a site")
    enable_parser.add_argument("--name", required=True)

    disable_parser = subparsers.add_parser("disable", help="Disable a site")
    disable_parser.add_argument("--name", required=True)

    update_parser = subparsers.add_parser("update", help="Update an existing site")
    update_parser.add_argument("--name", required=True)
    _add_site_arguments(update_parser, include_name=False)

    return parser


def _add_site_arguments(parser: argparse.ArgumentParser, include_name: bool = True) -> None:
    if include_name:
        parser.add_argument("--name", required=True)
    parser.add_argument("--url", required=include_name)
    parser.add_argument("--expected-status", action="append", type=int, dest="expected_status")
    parser.add_argument("--expected-text")
    parser.add_argument("--latency-threshold-ms", type=int)
    parser.add_argument("--check-cert", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--cert-warn-days", type=int)
    parser.add_argument("--enabled", dest="enabled", action="store_true", default=None)
    parser.add_argument("--disabled", dest="enabled", action="store_false")


def main() -> None:
    args = build_parser().parse_args()
    settings = load_settings()
    sites_file = Path(args.sites_file) if args.sites_file else settings.sites_file
    sites = load_sites(sites_file)

    if args.command == "list":
        print_sites(sites)
        return

    if args.command == "add":
        site = parse_site(_site_payload_from_args(args))
        if any(existing.name.lower() == site.name.lower() for existing in sites):
            raise SystemExit(f"Site already exists: {site.name}")
        sites.append(site)
        save_sites(sites_file, sorted(sites, key=lambda item: item.name.lower()))
        print(f"Added site: {site.name}")
        return

    if args.command == "remove":
        updated = [site for site in sites if site.name.lower() != args.name.lower()]
        if len(updated) == len(sites):
            raise SystemExit(f"Site not found: {args.name}")
        save_sites(sites_file, updated)
        print(f"Removed site: {args.name}")
        return

    if args.command in {"enable", "disable", "update"}:
        target = find_site(sites, args.name)
        if target is None:
            raise SystemExit(f"Site not found: {args.name}")

        if args.command == "enable":
            target.enabled = True
            save_sites(sites_file, sites)
            print(f"Enabled site: {target.name}")
            return

        if args.command == "disable":
            target.enabled = False
            save_sites(sites_file, sites)
            print(f"Disabled site: {target.name}")
            return

        merged = target.as_json()
        for key, value in _site_payload_from_args(args).items():
            if value is not None:
                merged[key] = value
        updated_site = parse_site(merged)
        for index, site in enumerate(sites):
            if site.name.lower() == target.name.lower():
                sites[index] = updated_site
                break
        save_sites(sites_file, sites)
        print(f"Updated site: {updated_site.name}")
        return

    raise SystemExit("Unknown command")


def _site_payload_from_args(args: argparse.Namespace) -> dict:
    payload = {
        "name": args.name,
        "url": getattr(args, "url", None),
        "expected_status": getattr(args, "expected_status", None),
        "expected_text": getattr(args, "expected_text", None),
        "latency_threshold_ms": getattr(args, "latency_threshold_ms", None),
        "check_cert": getattr(args, "check_cert", None),
        "cert_warn_days": getattr(args, "cert_warn_days", None),
        "enabled": getattr(args, "enabled", None),
    }
    return {key: value for key, value in payload.items() if value is not None}


def find_site(sites: list[SiteConfig], name: str) -> SiteConfig | None:
    for site in sites:
        if site.name.lower() == name.lower():
            return site
    return None


def print_sites(sites: list[SiteConfig]) -> None:
    if not sites:
        print("No sites configured.")
        return
    for site in sorted(sites, key=lambda item: item.name.lower()):
        status = "enabled" if site.enabled else "disabled"
        print(f"- {site.name} | {status} | {site.url}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
