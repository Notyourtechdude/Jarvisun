"""Noninteractive local setup and operation. Token printing is an explicit command."""
import argparse
import asyncio
import json
import os
import platform
import shutil
import sys
import tomllib
from pathlib import Path

from .config import Settings
from .security import owner_token


def initialize(directory: Path, vault_roots=()):
    directory = directory.expanduser().absolute()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    config = directory / "config.toml"
    if config.exists():
        Settings.load(config)
        return config
    settings = Settings(data_dir=directory / "state", workspace=directory / "workspace",
                        vault_roots=list(vault_roots) or [directory / "workspace"])
    owner_token(settings)
    values = settings.model_dump(mode="json")
    # TOML arrays/string/bool/number literals are compatible with these JSON values.
    content = "# Non-secret configuration. Paths are relative to this file.\n"
    values["data_dir"], values["workspace"] = "state", "workspace"
    if not vault_roots:
        values["vault_roots"] = ["workspace"]
    for key, value in values.items():
        content += f"{key} = {json.dumps(value, ensure_ascii=False)}\n"
    tomllib.loads(content)
    fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(content)
    return config


def doctor(settings: Settings):
    import importlib.util

    from .models import LocalModel
    return {"python": platform.python_version(), "platform": platform.platform(), "cpus": os.cpu_count(),
            "workspace": str(settings.workspace), "data_dir": str(settings.data_dir),
            "model": asyncio.run(LocalModel(settings).status()),
            "ffmpeg": bool(shutil.which("ffmpeg")), "docker": bool(shutil.which("docker")),
            "playwright": importlib.util.find_spec("playwright") is not None,
            "credentials": {name: "set" if os.environ.get(name) else "missing" for name in (
                "TELEGRAM_BOT_TOKEN", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "WHATSAPP_ACCESS_TOKEN",
                "BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD", settings.model_key_env)},
            "note": "Executable/credential presence is not live capability verification."}


def parser():
    root = argparse.ArgumentParser(prog="jarvis", description="Local personal operator")
    actions = root.add_subparsers(dest="action", required=True)
    init = actions.add_parser("init", help="Create private state/config without overwriting existing files")
    init.add_argument("--directory", type=Path, default=Path(".jarvis"))
    init.add_argument("--vault", type=Path, action="append", default=[])
    for name in ("serve", "doctor", "token", "index", "ask", "backup", "telegram", "frontier"):
        command = actions.add_parser(name)
        command.add_argument("--config", type=Path, default=Path(".jarvis/config.toml"))
        if name == "serve":
            command.add_argument("--host", default="127.0.0.1")
            command.add_argument("--port", type=int, default=8787)
        elif name == "ask":
            command.add_argument("message")
        elif name == "backup":
            command.add_argument("--output", type=Path, required=True)
        elif name == "frontier":
            command.add_argument("--enable", action="store_true",
                                 help="Explicitly send stdin prompt to the trusted owner Codex CLI")
            command.add_argument("--executable", type=Path)
            command.add_argument("--auth-home", type=Path)
    restore = actions.add_parser("restore")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--directory", type=Path, required=True)
    return root


async def telegram_loop(settings):
    import httpx

    from .app import create_app
    from .extensions.channels import Telegram
    from .extensions.common import credential
    from .messaging import PollCursor
    channel = Telegram(owner_chat_id=settings.telegram_chat_id, owner_user_id=settings.telegram_user_id or None)
    token = credential("TELEGRAM_BOT_TOKEN")
    if not channel.available()["available"]:
        raise ValueError("Telegram binding unavailable")
    core = create_app(settings).state.core
    cursor = PollCursor(core.store, token + ":" + channel.owner_chat_id + ":" + channel.owner_user_id)
    offset = cursor.offset()
    # Suppress request logging: Telegram credentials appear in its official URL.
    import logging
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    async with httpx.AsyncClient(timeout=35, trust_env=False, follow_redirects=False) as client:
        while True:
            try:
                response = await client.post(f"https://api.telegram.org/bot{token}/getUpdates",
                                             json={"offset": offset, "timeout": 20,
                                                   "allowed_updates": ["message"]})
                response.raise_for_status()
                data = response.json()
                if not data.get("ok") or not isinstance(data.get("result"), list):
                    raise ValueError("Telegram poll failed")
                for update in data["result"]:
                    if not isinstance(update, dict) or type(update.get("update_id")) is not int:
                        continue
                    if not cursor.claim(update["update_id"]):
                        continue
                    offset = cursor.offset()
                    try:
                        channel.check_owner(update)
                    except ValueError:
                        continue
                    message = update.get("message", {}).get("text", "")
                    if not isinstance(message, str) or not 1 <= len(message) <= 6000:
                        continue
                    # Messaging interface is read-only: delegation is web/local only.
                    if message.startswith("/do "):
                        answer = "Computer delegation is available through your authenticated local web interface."
                    else:
                        result = await core.chat(message)
                        answer = result["answer"][:3900]
                    from starlette.concurrency import run_in_threadpool
                    await run_in_threadpool(channel.send, answer)
            except (httpx.HTTPError, ValueError):
                print("Telegram temporarily unavailable; retrying after 5 seconds", file=sys.stderr)
                await asyncio.sleep(5)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.action == "init":
            print(f"Configuration: {initialize(args.directory, args.vault)}")
            print("Access token is private. Use the explicit 'jarvis token' command to view it locally.")
            return
        if args.action == "restore":
            from .portability import restore
            settings = restore(args.archive, args.directory)
            print(json.dumps({"restored": str(args.directory), "data_dir": str(settings.data_dir)}))
            return
        settings = Settings.load(args.config)
        if args.action == "token":
            print(owner_token(settings))
        elif args.action == "doctor":
            print(json.dumps(doctor(settings), indent=2))
        elif args.action == "serve":
            import uvicorn

            from .app import create_app
            uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="warning")
        elif args.action == "index":
            from .vault import Vault
            settings.prepare()
            print(json.dumps(Vault(settings.vault_roots, settings.data_dir / "vault.sqlite3").index()))
        elif args.action == "ask":
            from .app import create_app
            app = create_app(settings)
            print(json.dumps(asyncio.run(app.state.core.chat(args.message)), ensure_ascii=False, indent=2))
        elif args.action == "backup":
            from .portability import backup
            print(json.dumps(backup(settings, args.output), indent=2))
        elif args.action == "telegram":
            asyncio.run(telegram_loop(settings))
        elif args.action == "frontier":
            from .codex_provider import MAX_PROMPT_BYTES, CodexProvider
            provider = CodexProvider(settings.workspace, args.executable, args.auth_home, enabled=args.enable)
            # Explicit stdin only; no repository, vault, transcript or environment context is appended.
            prompt = sys.stdin.buffer.read(MAX_PROMPT_BYTES + 1).decode("utf-8")
            print(json.dumps(provider.run(prompt), ensure_ascii=False))
    except KeyboardInterrupt:
        return
    except Exception:
        # Never echo provider exception payloads or credentials.
        print("Jarvis could not complete the command. Check configuration, permissions, and capability readiness.",
              file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
