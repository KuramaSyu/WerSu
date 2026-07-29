#!/usr/bin/env python3
"""Generate .env.prod from .env.prod.default for the WerSu prod stack.

Walks the user through every required value (domain, ACME email,
postgres password, Discord creds, ...), fills in cryptographically
random secrets, and writes a finished .env.prod next to
docker-compose.prod.yaml.

Re-running is safe: it never overwrites an existing .env.prod without
explicit confirmation.

The template uses two kinds of placeholders:

  @ask:<KEY>@     filled with a value the user provides
  @random:<KEY>@  filled with a fresh secrets.token_hex(N)

Anything else in the template is passed through verbatim, including
comments and ${DOMAIN}-style references that docker-compose
interpolates at runtime.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).resolve().parent / ".env.prod.template"
OUTPUT = REPO_ROOT / ".env.prod"

ASK_PATTERN = re.compile(r"@ask:([A-Z][A-Z0-9_]*)@")
RANDOM_PATTERN = re.compile(r"@random:([A-Z][A-Z0-9_]*)@")


@dataclass
class Field:
    """One user-prompted value."""

    key: str
    prompt: str
    default: str = ""
    hint: str = ""
    # Validation mode. ``required`` re-prompts on empty; ``optional``
    # accepts empty (falls back to ``default``); ``email`` / ``domain``
    # additionally format-check the value.
    mode: str = "required"
    group: str = "Values"


def rand_hex(nbytes: int) -> str:
    """Generate nbytes of randomness as hex. Drop-in for openssl rand -hex N."""
    return secrets.token_hex(nbytes)


# Field definitions. Edit this list to add or change prompts.
FIELDS: list[Field] = [
    Field("IMAGE_TAG", "Image tag", default="latest",
          hint="latest for dev, semver for releases", mode="optional",
          group="Public config"),
    Field("DOMAIN", "Apex domain", hint="e.g. inu-the-bot.com",
          mode="domain", group="Public config"),
    Field("FRONTEND_HOST", "Frontend hostname",
          hint="blank to use wersu.<DOMAIN>", mode="optional",
          group="Public config"),

    Field("DISCORD_CLIENT_ID", "Discord client ID", mode="optional",
          group="Public config"),
    Field("DISCORD_CLIENT_SECRET", "Discord client secret", mode="optional",
          group="Public config"),

    Field("POSTGRES_USER", "Postgres user", default="postgres",
          mode="optional", group="Storage credentials"),
    Field("POSTGRES_PASSWORD", "Postgres password", hint="blank = random",
          mode="optional", group="Storage credentials"),
    Field("POSTGRES_DB", "Postgres database name", default="db",
          mode="optional", group="Storage credentials"),

    Field("GARAGE_DEFAULT_ACCESS_KEY", "Garage access key",
          hint="leave blank to use the dev defaults", mode="optional",
          group="Storage credentials"),
    Field("GARAGE_DEFAULT_SECRET_KEY", "Garage secret key",
          hint="leave blank to use the dev defaults", mode="optional",
          group="Storage credentials"),
    Field("GARAGE_DEFAULT_BUCKET", "Garage bucket name", default="garage",
          mode="optional", group="Storage credentials"),
]

# Random fields. The values are generated unconditionally at the end
# (these are the secrets that must be unpredictable).
RANDOM_FIELDS: dict[str, int] = {
    "SPICEDB_PASSWORD": 32,
    "GRPC_SPICEDB_CREDENTIALS": 32,
    "JWT_SECRET": 64,
    "SESSION_SECRET": 64,
}


# ---------- Validators ----------

def is_email(value: str) -> bool:
    # Loose check - just needs something@host.tld with no whitespace.
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value))


def is_domain(value: str) -> bool:
    # RFC 1035-ish: labels of alnum/-, separated by dots, ending in a
    # TLD of at least two letters. No scheme, no path, no port.
    return bool(re.match(
        r"^(?=.{1,253}$)"
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
        r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
        r"\.[A-Za-z]{2,}$",
        value,
    ))


def validate(field: Field, value: str) -> str | None:
    """Return None if ``value`` is OK, else an error message."""
    if field.mode == "optional":
        return None
    if not value:
        return f"{field.key} is required"
    if field.mode == "email" and not is_email(value):
        return f"{value!r} doesn't look like an email"
    if field.mode == "domain" and not is_domain(value):
        return f"{value!r} doesn't look like a domain"
    return None


# ---------- Prompt loop ----------

def prompt(field: Field) -> str:
    """Ask once. Empty input falls back to ``field.default``."""
    parts = [field.prompt]
    if field.hint:
        parts.append(f"({field.hint})")
    if field.default:
        parts.append(f"[{field.default}]")
    label = " ".join(parts) + ": "

    raw = input(label).strip()
    return raw or field.default


def collect_values() -> dict[str, str]:
    """Run the full prompt loop, including summary + edit step."""
    values: dict[str, str] = {}

    while True:
        current_group = None
        for field in FIELDS:
            # Print a section header when the group changes, so the
            # terminal output mirrors the block layout of the .env file.
            if field.group != current_group:
                print()
                print(f"--- {field.group} ---")
                current_group = field.group

            # FRONTEND_HOST defaults to "wersu.<DOMAIN>" - it always
            # lives on a dedicated subdomain so the apex can host
            # other things later.
            default = field.default
            if field.key == "FRONTEND_HOST" and not values.get("FRONTEND_HOST"):
                domain = values.get("DOMAIN", "")
                default = f"wersu.{domain}" if domain else default

            while True:
                value = prompt(field)
                err = validate(field, value)
                if err is None:
                    break
                print(f"  {err}", file=sys.stderr)
            values[field.key] = value

        print()
        print("Summary:")
        pw_display = "(set)" if values["POSTGRES_PASSWORD"] else "(random)"
        current_group = None
        for f in FIELDS:
            if f.group != current_group:
                print(f"  [{f.group}]")
                current_group = f.group
            displayed = values[f.key]
            if f.key == "POSTGRES_PASSWORD" and not displayed:
                displayed = pw_display
            print(f"    {f.key:30s} {displayed}")
        print()

        action = input("Type 'edit <KEY>' to fix one, or enter to write the file: ").strip()
        if not action:
            return values
        if action.startswith("edit "):
            key = action[5:].strip()
            available_groups: dict[str, list[str]] = {}
            for f in FIELDS:
                available_groups.setdefault(f.group, []).append(f.key)
            available = ", ".join(
                f"{g}: {', '.join(keys)}"
                for g, keys in available_groups.items()
            )
            if key not in values:
                print(f"  unknown key: {key}", file=sys.stderr)
                print(f"  available: {available}", file=sys.stderr)
                continue
            # Re-prompt just that one field with the current value as
            # the new default. Optional fields clear the default so
            # the user can blank them out.
            for f in FIELDS:
                if f.key == key:
                    f.default = values[key]
                    if f.mode == "optional" and key != "POSTGRES_PASSWORD":
                        f.default = ""
                    while True:
                        v = prompt(f)
                        err = validate(f, v)
                        if err is None:
                            break
                        print(f"  {err}", file=sys.stderr)
                    values[key] = v
                    break
            continue
        print("  unknown action, type 'edit <KEY>' or press enter", file=sys.stderr)


# ---------- Substitution ----------

def substitute(text: str, values: dict[str, str]) -> str:
    """Replace @ask:<KEY>@ and @random:<KEY>@ placeholders.

    After the ask/random passes, also expand any ``${OTHER_KEY}`` shell-style
    references in the rendered values against ``values``. This lets the
    template compose values out of other values (e.g. ``DATABASE_DSN``
    built from ``POSTGRES_USER``) without relying on the runtime to
    expand them - docker compose's ``${VAR}`` substitution does not
    happen inside ``env_file`` values.
    """

    def replace_ask(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"template references unknown ask key: {key}")
        return values[key]

    text = ASK_PATTERN.sub(replace_ask, text)
    text = RANDOM_PATTERN.sub(
        lambda m: rand_hex(RANDOM_FIELDS[m.group(1)]), text
    )

    leftover = re.findall(r"@(ask|random):[A-Z][A-Z0-9_]*@", text)
    if leftover:
        raise ValueError(f"leftover placeholders after substitution: {leftover}")

    # Expand ${OTHER_KEY} references. Only references whose KEY is in
    # ``values`` are touched; literal ${...} that we don't know about
    # are passed through (so the runtime can still see ${DOMAIN} for
    # docker compose's own interpolation if it ever reads this file).
    def expand_braces(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in values:
            return values[key]
        return match.group(0)

    return re.sub(r"\$\{([A-Z][A-Z0-9_]*)\}", expand_braces, text)


# ---------- Entry point ----------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=OUTPUT,
        help="Where to write the generated env (default: .env.prod at repo root).",
    )
    parser.add_argument(
        "--template", type=Path, default=TEMPLATE,
        help="Template file to read (default: .env.prod.default at repo root).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite .env.prod without prompting if it already exists.",
    )
    args = parser.parse_args()

    if not args.template.is_file():
        print(f"error: template not found at {args.template}", file=sys.stderr)
        return 1

    print("WerSu prod env generator")
    print("========================")
    print()
    print(f"This will write {args.output}.")
    print("Press enter to accept defaults shown in [brackets].")
    print()

    values = collect_values()

    # POSTGRES_PASSWORD: if the user left it blank, fill in a random.
    if not values.get("POSTGRES_PASSWORD"):
        values["POSTGRES_PASSWORD"] = rand_hex(32)

    if args.output.is_file() and not args.force:
        confirm = input(f"{args.output} already exists. Overwrite? [y/N] ").strip()
        if confirm.lower() not in ("y", "yes"):
            print(f"aborted, {args.output} left untouched")
            return 0

    rendered = substitute(args.template.read_text(), values)
    args.output.write_text(rendered)

    # Tight permissions so other users on the host can't read the
    # secrets. Best-effort; ignore failures (e.g. on Windows).
    try:
        os.chmod(args.output, 0o600)
    except OSError:
        pass

    print()
    print(f"wrote {args.output} (mode 600 where supported)")
    print("deploy with:")
    print("  docker compose -f docker-compose.prod.yaml --env-file .env.prod up -d")
    return 0


if __name__ == "__main__":
    sys.exit(main())
