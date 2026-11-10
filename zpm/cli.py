"""Explicit operator commands; planning and status never execute repository code."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .controller import Controller
from .models import Binding, ConfigurationError, ProviderConfig
from .provider import OpenJulesProvider, ProviderError
from .store import Store


def read_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConfigurationError("cannot read the requested JSON configuration") from None


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="zpm", description="Repository enrollment and desired worker capacity")
    result.add_argument("--state", default=".zpm/state.sqlite3", help="local SQLite state path; retain across restarts")
    result.add_argument("--deployment-id", help="set on initial state creation, or verify an existing deployment")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="initialize persistent deployment identity")
    for command in ("validate", "enroll"):
        commands.add_parser(command).add_argument("binding", help="versioned repository binding JSON")
    for command in ("target", "resume"):
        sub = commands.add_parser(command)
        sub.add_argument("repository")
        sub.add_argument("workers", type=int)
    for command in ("pause", "remove"):
        commands.add_parser(command).add_argument("repository")
    commands.add_parser("status")
    commands.add_parser("plan")
    for command in ("check", "reconcile"):
        sub = commands.add_parser(command)
        sub.add_argument("--provider", required=True, help="OpenJules endpoint, token reference and capacity ceiling JSON")
        if command == "reconcile":
            sub.add_argument("--watch", action="store_true", help="hold controller ownership and poll with bounded delay")
            sub.add_argument("--interval", type=float, default=5.0)
            sub.add_argument("--max-actions", type=int, default=16, help="mutation batch limit per tick, not a worker-count ceiling")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "validate":
            # No database, credentials, model or provider is accessed.
            output = {"valid": True, "binding": Binding.parse(read_json(args.binding)).to_dict(), "remote_access_checked": False}
        else:
            if args.command not in {"init", "enroll"} and not Path(args.state).is_file():
                raise ConfigurationError("deployment state does not exist; run init or enroll first")
            with Store(args.state, args.deployment_id) as store:
                if args.command == "init":
                    output = {"schema_version": 1, "deployment_id": store.deployment_id}
                elif args.command == "enroll":
                    output = {"deployment_id": store.deployment_id, "binding": store.enroll(read_json(args.binding))}
                elif args.command in {"target", "resume", "pause", "remove"}:
                    output = store.change(args.repository, args.command, getattr(args, "workers", None))
                elif args.command in {"status", "plan"}:
                    output = Controller(store).plan()
                else:
                    config = ProviderConfig.parse(read_json(args.provider))
                    controller = Controller(store, OpenJulesProvider(config), config.max_workers)
                    if args.command == "check":
                        output = controller.check()
                    elif args.watch:
                        controller.run(args.interval, args.max_actions)
                        return 0
                    else:
                        output = controller.tick(args.max_actions)
        print(json.dumps(output, sort_keys=True, indent=2))
        return 0
    except (ConfigurationError, ProviderError, OSError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
