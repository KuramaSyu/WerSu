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
from dataclasses import dataclass
from pathlib import Path

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
    # Validation mode. ``required`` re-prompts on empty; ``optional``
    # accepts empty (falls back to ``default``); ``email`` / ``domain``
    # additionally format-check the value.
    mode: str = "required"
    group: str = "Values"


def rand_hex(nbytes: int) -> str:
    """Generate nbytes of randomness as hex. Drop-in for openssl rand -hex N."""
    return secrets.token_hex(nbytes)


# Per-field help blocks. Printed before the prompt for the field that
# needs more than a one-liner. Keep these readable in a terminal -
# short paragraphs, blank lines between steps.
HELP: dict[str, str] = {
    "DOMAIN": (
        "Your apex domain, e.g. inu-the-bot.com.\n"
        "Traefik will route the four subdomains below to the matching\n"
        "services on this host:\n"
        "  - wersu-api.<DOMAIN>  -> wersu-rest\n"
        "  - wersu-ws.<DOMAIN>   -> hocuspocus\n"
        "  - wersu.<DOMAIN>      -> wersu-frontend (FRONTEND_HOST)\n"
        "Create A (or AAAA) records for each, all pointing at this\n"
        "host's public IP. Port 80 must be reachable from the internet\n"
        "so Let's Encrypt can issue the HTTPS cert."
    ),
    "FRONTEND_HOST": (
        "Hostname the React frontend is served on. Defaults to\n"
        "wersu.<DOMAIN>. Change this if your frontend lives somewhere\n"
        "else (e.g. a sub-path on the apex)."
    ),
    "DISCORD_CLIENT_ID": (
        "Discord OAuth is how users log in. To set it up:\n"
        "  1. Open https://discord.com/developers/applications and\n"
        "     create a new application (e.g. named 'WerSu Login').\n"
        "  2. In the left panel open OAuth2.\n"
        "  3. Add a redirect:\n"
        "       https://wersu-api.<DOMAIN>/api/auth/discord/callback\n"
        "     (use http://localhost if you only want to test locally\n"
        "     first).\n"
        "  4. Copy the Client ID. We need the secret on the next prompt.\n"
        "Both ID and Secret are required - the script rejects runs\n"
        "where only one is set."
    ),
    "DISCORD_CLIENT_SECRET": (
        "Same Discord app as the Client ID. The Secret sits next to\n"
        "the ID on the OAuth2 page - click 'Reset Secret' if you've\n"
        "never copied it before."
    ),
    "POSTGRES_PASSWORD": (
        "Password for the Postgres role that owns the app database.\n"
        "Leave blank to auto-generate a 64-char random value. Or set\n"
        "your own with: openssl rand -hex 32"
    ),
    "IMAGE_TAG": (
        "Tag of the three app images to pull from docker.io/kuramasyu/*.\n"
        "  - 'dev' tracks the most recent build of the default branch.\n"
        "  - 'vX.Y.Z' pins a specific release. Bump this in\n"
        "    .env.prod and re-run `docker compose up -d` to deploy."
    ),
}

# Field definitions. Edit this list to add or change prompts.
FIELDS: list[Field] = [
    Field("IMAGE_TAG", "Image tag", default="dev", mode="optional",
          group="Public config"),
    Field("DOMAIN", "Apex domain", mode="domain", group="Public config"),
    Field("FRONTEND_HOST", "Frontend hostname", mode="optional",
          group="Public config"),

    Field("DISCORD_CLIENT_ID", "Discord client ID", mode="required",
          group="Public config"),
    Field("DISCORD_CLIENT_SECRET", "Discord client secret", mode="required",
          group="Public config"),

    Field("POSTGRES_USER", "Postgres user", default="postgres",
          mode="optional", group="Storage credentials"),
    Field("POSTGRES_PASSWORD", "Postgres password", default="[autogenerate]",
          mode="optional", group="Storage credentials"),
    Field("POSTGRES_DB", "Postgres database name", default="db",
          mode="optional", group="Storage credentials"),
]

# Random fields. The values are generated unconditionally at the end
# (these are the secrets that must be unpredictable).
# Size is in bytes; the rendered value is twice that many hex chars.
RANDOM_FIELDS: dict[str, int] = {
    "GRPC_SPICEDB_CREDENTIALS": 32,
    "JWT_SECRET": 64,
    "SESSION_SECRET": 64,
    # Garage S3 credentials: 16-byte access key (32 hex chars),
    # 32-byte secret (64 hex chars). Garage's own `key create`
    # produces similar sizes.
    "GARAGE_DEFAULT_ACCESS_KEY": 16,
    "GARAGE_DEFAULT_SECRET_KEY": 32,
}

# Static default values for things that need to match a literal in
# pgvector_init.sql but shouldn't be asked of the user. SPICEDB_PASSWORD
# is the password pgvector_init.sql uses when creating the spicedb
# Postgres role; it must agree with the password SpiceDB's
# --datastore-conn-uri uses.
STATIC_DEFAULTS: dict[str, str] = {
    "SPICEDB_PASSWORD": "spicedb",
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


def is_discord_client_id(value: str) -> bool:
    # Discord client IDs are snowflakes: 17-20 digit numeric strings.
    return bool(re.fullmatch(r"\d{17,20}", value))


def is_discord_client_secret(value: str) -> bool:
    # Discord secrets are ~30+ chars of url-safe base64-ish noise. We
    # just want to catch typos (truncated copy-paste, trailing spaces,
    # accidentally pasted the client ID again), not pin the format.
    return len(value) >= 24 and " " not in value


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
    if field.key == "DISCORD_CLIENT_ID" and not is_discord_client_id(value):
        return (f"{value!r} doesn't look like a Discord client ID "
                f"(expected 17-20 digit numeric string)")
    if field.key == "DISCORD_CLIENT_SECRET" and not is_discord_client_secret(value):
        return (f"{value!r} doesn't look like a Discord client secret "
                f"(expected ~30+ chars, no whitespace)")
    return None


# ---------- Prompt loop ----------

def print_help(key: str, values: dict[str, str]) -> None:
    """Print the multi-line help block for a field, if one is defined.

    Any ``<KEY>`` placeholder in the help text is replaced with the
    matching value from ``values`` (only if it's already been asked).
    Lets the Discord step reference the user's domain automatically
    instead of asking them to substitute ``<DOMAIN>`` themselves.
    """
    text = HELP.get(key)
    if not text:
        return

    def sub(match: re.Match[str]) -> str:
        k = match.group(1)
        return values.get(k, match.group(0))

    text = re.sub(r"<([A-Z][A-Z0-9_]*)>", sub, text)

    print()
    for line in text.splitlines():
        if line:
            print(f"  {line}")
        else:
            print()


def prompt(field: Field, default_override: str = "") -> str:
    """Ask once. Empty input falls back to ``default_override`` if set,
    else ``field.default``."""
    parts = [field.prompt]
    default = default_override or field.default
    if default:
        parts.append(f"[{default}]")
    label = " ".join(parts) + ": "

    raw = input(label).strip()
    return raw or default


def collect_values() -> dict[str, str]:
    """Run the full prompt loop, including summary + edit step."""
    values: dict[str, str] = {}

    while True:
        # Index into FIELDS. Using a manual index lets the Discord
        # cross-field check restart the loop from the top when the user
        # only fills in one of the two values.
        i = 0
        current_group = None
        while i < len(FIELDS):
            field = FIELDS[i]

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
            domain = values.get("DOMAIN", "")
            if field.key == "FRONTEND_HOST" and not values.get("FRONTEND_HOST"):
                default = f"wersu.{domain}" if domain else default

            print_help(field.key, values)

            while True:
                value = prompt(field, default_override=default)
                err = validate(field, value)
                if err is None:
                    break
                print(f"  {err}", file=sys.stderr)
            values[field.key] = value

            # Cross-field: Discord ID and Secret must be set together.
            # Only check after the SECOND of the pair (Secret) has been
            # answered - by that point both values have been entered, so
            # a missing one is a real problem rather than the user just
            # not having seen the next prompt yet.
            if field.key == "DISCORD_CLIENT_SECRET":
                cid = values.get("DISCORD_CLIENT_ID", "")
                csec = values.get("DISCORD_CLIENT_SECRET", "")
                if (cid and not csec) or (csec and not cid):
                    print(
                        "  DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET "
                        "must both be set. Re-asking both...",
                        file=sys.stderr,
                    )
                    values["DISCORD_CLIENT_ID"] = ""
                    values["DISCORD_CLIENT_SECRET"] = ""
                    i = 0
                    current_group = None
                    continue

            i += 1

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
    """Replace @ask:<KEY>@ and @random:<KEY>@ placeholders, then expand
    any ``${OTHER_KEY}`` shell-style references against the rendered
    output. This lets the template compose values out of other values
    (e.g. ``DATABASE_DSN`` built from ``POSTGRES_USER``) without
    relying on the runtime to expand them - docker compose's
    ``${VAR}`` substitution does not happen inside ``env_file``
    values.

    Why we re-scan the rendered output instead of using the ``values``
    dict for ``${...}`` expansion: ``@random:<KEY>@`` placeholders
    generate values during substitution that are never written back
    into ``values``. Re-parsing the rendered text picks those up so a
    downstream ``S3_REGION=${GARAGE_DEFAULT_BUCKET}`` (literal) still
    expands to the auto-generated bucket name, etc.
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

    # Build the ${KEY} lookup from the rendered output. We only pick
    # up real ``KEY=value`` lines so commented-out placeholders (with
    # ``#`` prefix) and unrelated text don't leak in.
    derived: dict[str, str] = {}
    for line in text.splitlines():
        line = line.lstrip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", k):
            derived[k] = v

    # Plus the user-provided values (so derived values can compose
    # against both - though in practice the rendered lines win).
    derived.update(values)

    def expand_braces(match: re.Match[str]) -> str:
        key = match.group(1)
        return derived.get(key, match.group(0))

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

    # POSTGRES_PASSWORD: auto-generate if the user left it blank or
    # accepted the [autogenerate] default at the prompt.
    if not values.get("POSTGRES_PASSWORD") or values["POSTGRES_PASSWORD"] == "[autogenerate]":
        values["POSTGRES_PASSWORD"] = rand_hex(32)

    # Static defaults for any auto-generated / non-asked values. These
    # are exposed via ${KEY} expansion in substitute() so downstream
    # lines that reference them (e.g. S3_REGION=${GARAGE_DEFAULT_BUCKET})
    # get a real value. The @random:<KEY>@ placeholders still get their
    # own freshly-generated random values during substitution.
    for key, default in STATIC_DEFAULTS.items():
        values.setdefault(key, default)
    values.setdefault("GARAGE_DEFAULT_BUCKET", "bucket")

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
