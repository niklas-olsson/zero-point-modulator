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
from .registry import configuration as registry_configuration, decode_json as registry_json
from .store import Store


def read_json(path: str, *, deployment: bool = False, registry: bool = False) -> Any:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        if registry:
            return registry_json(raw)
        if deployment:
            from .deployment import _decode_configuration_json

            return _decode_configuration_json(raw)
        return json.loads(raw)
    except ConfigurationError:
        raise
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
    registry = commands.add_parser("registry", help="prepare disabled destinations without remote access")
    registry_commands = registry.add_subparsers(dest="registry_command", required=True)
    for command in ("init", "import", "validate", "migrate"):
        sub = registry_commands.add_parser(command)
        sub.add_argument("configuration", help="strict version 2 disabled-destination JSON")
        if command == "migrate":
            sub.add_argument("--backup", required=True, help="new backup file in an existing local directory")
            sub.add_argument("--offline", action="store_true", help="assert all old controllers and configuration writers are stopped")
    registry_commands.add_parser("status")
    deployment = commands.add_parser("deployment", help="prepare or verify an explicit private single-host installation")
    deployment_commands = deployment.add_subparsers(dest="deployment_command", required=True)
    prepare = deployment_commands.add_parser("prepare", help="write configuration without installing or starting services")
    prepare.add_argument("configuration", help="versioned operator deployment JSON")
    prepare.add_argument("--directory", required=True, help="new private directory outside source checkouts")
    deployment_commands.add_parser("verify", help="offline source and configuration integrity check").add_argument("directory")
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
        if args.command == "deployment":
            from .deployment import prepare, verify

            output = (prepare(read_json(args.configuration, deployment=True), args.directory)
                      if args.deployment_command == "prepare" else verify(args.directory))
        elif args.command == "validate":
            # No database, credentials, model or provider is accessed.
            output = {"valid": True, "binding": Binding.parse(read_json(args.binding)).to_dict(), "remote_access_checked": False}
        elif args.command == "registry":
            command = args.registry_command
            if command == "validate":
                registry_configuration(read_json(args.configuration, registry=True))
                output = {"valid": True, "schema_version": 2, "execution_enabled": False, "remote_access_checked": False}
            elif command == "init":
                with Store.initialize_registry(args.state, read_json(args.configuration, registry=True), args.deployment_id) as store:
                    output = store.registry_status()
            else:
                with Store(args.state, args.deployment_id, create=False) as store:
                    if command == "import":
                        output = store.import_registry(read_json(args.configuration, registry=True))
                    elif command == "migrate":
                        output = store.migrate_registry(read_json(args.configuration, registry=True), args.backup, offline=args.offline)
                    else:
                        output = store.registry_status()
        else:
            if args.command not in {"init", "enroll"} and not Path(args.state).is_file():
                raise ConfigurationError("deployment state does not exist; run init or enroll first")
            with Store(args.state, args.deployment_id, create=args.command in {"init", "enroll"}) as store:
                if args.command == "init":
                    store.require_legacy()
                    output = {"schema_version": 1, "deployment_id": store.deployment_id}
                elif args.command == "enroll":
                    output = {"deployment_id": store.deployment_id, "binding": store.enroll(read_json(args.binding))}
                elif args.command in {"target", "resume", "pause", "remove"}:
                    output = store.change(args.repository, args.command, getattr(args, "workers", None))
                elif args.command in {"status", "plan"}:
                    output = store.registry_status() if store.format_version == 2 else Controller(store).plan()
                else:
                    store.require_legacy()
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
