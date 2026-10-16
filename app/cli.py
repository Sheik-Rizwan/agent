"""Command-line user management. Works without the server running.

    python -m app.cli create-user alice --role admin     (prompts for the password)
    python -m app.cli list-users
    python -m app.cli reset-password alice
    python -m app.cli delete-user alice
"""

import argparse
import getpass
import sys

from dotenv import load_dotenv

from app.core.config import get_config
from app.core.exceptions import AppError
from app.repositories.session_repository import SessionRepository
from app.repositories.user_repository import UserRepository
from app.services.user_service import UserService


def _service() -> UserService:
    config = get_config()
    return UserService(UserRepository(config.data_dir), SessionRepository(config.data_dir), config)


def _ask_password() -> str:
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        raise AppError("Passwords don't match")
    return password


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Manage Agent users")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-user", help="Create a user")
    create.add_argument("username")
    create.add_argument("--role", choices=["admin", "user"], default="user")

    sub.add_parser("list-users", help="List users")

    reset = sub.add_parser("reset-password", help="Set a new password (logs the user out everywhere)")
    reset.add_argument("username")

    delete = sub.add_parser("delete-user", help="Delete a user (the last admin can't be deleted)")
    delete.add_argument("username")

    args = parser.parse_args(argv)
    users = _service()
    try:
        if args.command == "create-user":
            user = users.create(args.username, _ask_password(), args.role, created_by="cli")
            print(f"Created {user['role']} '{user['username']}'")
        elif args.command == "list-users":
            for user in users.list():
                print(f"{user['username']:<32} {user['role']:<6} last login: {user['last_login_at'] or 'never'}")
        elif args.command == "reset-password":
            users.set_password(args.username, _ask_password())
            print(f"Password updated for '{args.username}'")
        elif args.command == "delete-user":
            users.delete(args.username, acting_user="")
            print(f"Deleted '{args.username}'")
    except AppError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
