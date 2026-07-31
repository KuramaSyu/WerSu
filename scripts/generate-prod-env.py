#!/usr/bin/env python3
"""Generate .env.prod from .env.prod.template for the WerSu prod stack.

Walks the user through every required value (domain, postgres password,
Discord creds, ...), fills in cryptographically random secrets, and
writes a finished .env.prod next to docker-compose.prod.yaml.

Re-running is fully supported: previously-typed values become the
default at each prompt (press Enter to keep), and the script asks
once whether to regenerate any pre-existing secrets (default: keep).
This makes the script safe to run again whenever a new @random:<KEY>@
slot is added to the template - missing secrets are filled in,
existing ones are reused unless you ask otherwise.

The template uses two kinds of placeholders:

  @ask:<KEY>@     filled with a value the user provides
  @random:<KEY>@  filled with a fresh secrets.token_hex(N); reused
                  from the existing .env.prod if the user chose keep

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
# One level above the repo
DATA_ROOT = REPO_ROOT.parent
TEMPLATE = Path(__file__).resolve().parent / ".env.prod.template"
OUTPUT = REPO_ROOT / ".env.prod"
GARAGE_CONFIG_PATH = DATA_ROOT / "data" / "garage" / "config" / "garage.toml"

ASK_PATTERN = re.compile(r"@ask:([A-Z][A-Z0-9_]*)@")
RANDOM_PATTERN = re.compile(r"@random:([A-Z][A-Z0-9_]*)@")
ENV_LINE_PATTERN = re.compile(
    # KEY=value, KEY="value", or KEY='value'. We deliberately ignore
    # commented lines (leading whitespace + '#') and the rare quoted-
    # value edge case where '=' appears inside the value itself - the
    # template never uses that pattern.
    r"^\s*(?P<key>[A-Z][A-Z0-9_]*)\s*=\s*(?P<value>.*?)\s*$"
)


def parse_existing_env(path: Path) -> dict[str, str]:
    """Parse a previously-generated .env.prod back into a {KEY: value}
    dict. Comments (lines starting with '#', possibly after leading
    whitespace) and blank lines are skipped. Values are returned
    verbatim - no shell-style quote stripping - matching the on-disk
    shape the script writes."""
    if not path.is_file():
        return {}
    parsed: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.lstrip()
        if not line or line.startswith("#"):
            continue
        match = ENV_LINE_PATTERN.match(raw)
        if not match:
            continue
        parsed[match.group("key")] = match.group("value")
    return parsed


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
        "your own with: openssl rand -hex 32\n"
        "This prompt only appears when --manual-password is passed;\n"
        "otherwise the script auto-generates one with no prompt."
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
    Field("POSTGRES_PASSWORD", "Postgres password", default="",
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
    # Garage server secrets. The RPC secret is shared across all
    # cluster nodes (32 bytes hex = 64 chars). Admin + metrics tokens
    # are base64-style random strings; using 32 bytes hex matches the
    # shape `openssl rand -hex 32` produces, which is also what the
    # official quick-start suggests.
    "GARAGE_RPC_SECRET": 32,
    "GARAGE_ADMIN_TOKEN": 32,
    "GARAGE_METRICS_TOKEN": 32,
}

# Static default values for things that shouldn't be asked of the user
# but still need to be available via ${KEY} expansion in substitute().
# SPICEDB_PASSWORD must match the literal password pgvector_init.sql uses
# when creating the spicedb Postgres role. GARAGE_DEFAULT_BUCKET is the
# bucket name wired into the app code, the garage `--default-bucket`
# startup flag, and the imgproxy config - changing it breaks garage-
# backed uploads and image proxying.
STATIC_DEFAULTS: dict[str, str] = {
    "SPICEDB_PASSWORD": "spicedb",
    "GARAGE_DEFAULT_BUCKET": "garage",
}

# Embedded garage.toml - written to data/garage/config/garage.toml on
# every successful run. Living here (rather than as a checked-in file)
# makes it easy to regenerate the whole stack from scratch with one
# command, and keeps the secret-free config next to the script that
# produces the secrets it consumes.
#
# rpc_secret / admin_token / metrics_token are intentionally absent:
# the garage binary reads them from $GARAGE_RPC_SECRET /
# $GARAGE_ADMIN_TOKEN / $GARAGE_METRICS_TOKEN at process start.
GARAGE_CONFIG_TEMPLATE = """\
# Minimal Garage configuration for the WerSu prod stack.

metadata_dir = "/tmp/meta"
data_dir = "/tmp/data"
db_engine = "sqlite"

replication_factor = 1

# secrets are read from env file
rpc_bind_addr = "[::]:3901"
rpc_public_addr = "127.0.0.1:3901"

[s3_api]
s3_region = "garage"
api_bind_addr = "[::]:3900"
root_domain = ".s3.garage.localhost"

[s3_web]
bind_addr = "[::]:3902"
root_domain = ".web.garage.localhost"
index = "index.html"

[admin]
api_bind_addr = "[::]:3903"
"""


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


def collect_values(manual_password: bool = False) -> dict[str, str]:
    """Run the full prompt loop, including summary + edit step.

    POSTGRES_PASSWORD is only prompted for when ``manual_password`` is
    True; otherwise the script auto-generates a fresh random value and
    no row appears in the summary (run `--manual-password` to set one
    yourself).
    """
    values: dict[str, str] = {}

    while True:
        # Index into FIELDS. Using a manual index lets the Discord
        # cross-field check restart the loop from the top when the user
        # only fills in one of the two values.
        i = 0
        current_group = None
        while i < len(FIELDS):
            field = FIELDS[i]

            # In non-manual mode skip the POSTGRES_PASSWORD prompt
            # entirely - the value will be auto-generated below.
            if field.key == "POSTGRES_PASSWORD" and not manual_password:
                i += 1
                continue

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
        pw_value = values.get("POSTGRES_PASSWORD", "")
        pw_display = "(set)" if pw_value else "(random)"
        current_group = None
        for f in FIELDS:
            # Hide the POSTGRES_PASSWORD row entirely in non-manual
            # mode (it's auto-generated, not user-provided).
            if f.key == "POSTGRES_PASSWORD" and not manual_password:
                continue
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

def substitute(
    text: str,
    values: dict[str, str],
    existing_randoms: dict[str, str] | None = None,
) -> str:
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
    downstream ``AWS_ACCESS_KEY_ID=${GARAGE_DEFAULT_ACCESS_KEY}``
    (literal) still expands to the freshly-generated key, etc.

    ``existing_randoms`` lets re-runs preserve previously-generated
    secrets when the user answered "keep" to the regen prompt. Keys
    not present in ``existing_randoms`` (e.g. a brand-new @random slot
    that didn't exist in the previous .env.prod) fall through to
    fresh generation, so the script can also be used to backfill
    missing secrets.
    """

    existing_randoms = existing_randoms or {}

    def replace_ask(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"template references unknown ask key: {key}")
        return values[key]

    text = ASK_PATTERN.sub(replace_ask, text)

    def replace_random(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in existing_randoms:
            return existing_randoms[key]
        return rand_hex(RANDOM_FIELDS[key])

    text = RANDOM_PATTERN.sub(replace_random, text)

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
        help="Template file to read (default: .env.prod.template at repo root).",
    )
    parser.add_argument(
        "--manual-password", action="store_true",
        help=(
            "Prompt for POSTGRES_PASSWORD (with [autogenerate] default). "
            "Without this flag the password is auto-generated with no "
            "prompt."
        ),
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

    # On a re-run, parse the existing .env.prod so we can reuse
    # previously-typed ASK values and previously-generated secrets.
    # ASK values become Field.defaults (so the user can press Enter
    # to keep them); secrets are passed to substitute() to skip
    # regeneration when the user chooses "keep".
    existing = parse_existing_env(args.output)
    existing_randoms: dict[str, str] = {}
    if existing:
        for field in FIELDS:
            if field.key in existing:
                field.default = existing[field.key]
        # Anything that looks like a RANDOM_FIELD key gets queued for
        # reuse. We don't validate the value here - the template
        # authors the format, so the on-disk value will be either a
        # valid hex string (from a previous run) or absent (first run).
        for key in RANDOM_FIELDS:
            if key in existing:
                existing_randoms[key] = existing[key]

        print(f"Found existing {args.output}:")
        for key in (*(f.key for f in FIELDS), *RANDOM_FIELDS):
            if key in existing:
                print(f"  {key} = {existing[key]}")
        print()

    # Decide whether to keep or regenerate any pre-existing secrets.
    # "keep" (default) means every @random:<KEY>@ placeholder reuses
    # the value from the existing .env.prod; "regen" regenerates all
    # of them. Keys not present in the existing env are always freshly
    # generated regardless of the answer here, so the script can also
    # backfill secrets that were added after the first deploy.
    regenerate_secrets = False
    if existing_randoms:
        answer = input(
            f"{len(existing_randoms)} secret(s) detected. "
            "Regenerate or keep? [keep]: "
        ).strip().lower()
        if answer in ("regen", "regenerate", "r", "yes", "y"):
            regenerate_secrets = True
            existing_randoms = {}
        print()
    if regenerate_secrets:
        print("Will regenerate all existing secrets.")
    elif existing_randoms:
        print(f"Will keep {len(existing_randoms)} existing secret(s); "
              "missing ones will be generated fresh.")
    print()

    values = collect_values(manual_password=args.manual_password)

    # POSTGRES_PASSWORD: auto-generate when not provided. In manual
    # mode the user can either type one or leave it blank; in non-
    # manual mode the field was never prompted for. Either way, blank
    # means "make one". On a re-run, prefer the existing value - if
    # POSTGRES_PASSWORD was set by a previous run it's seeded into
    # values below before collect_values() runs, so the prompt loop
    # would have overwritten it only if the user typed something.
    if not values.get("POSTGRES_PASSWORD"):
        values["POSTGRES_PASSWORD"] = existing.get("POSTGRES_PASSWORD") or rand_hex(32)

    # Static defaults for any non-asked values. These are exposed via
    # ${KEY} expansion in substitute() so downstream lines that
    # reference them (e.g. AWS_ACCESS_KEY_ID=${GARAGE_DEFAULT_ACCESS_KEY})
    # get a real value. The @random:<KEY>@ placeholders still get their
    # own freshly-generated random values during substitution.
    # We don't override static defaults from the existing env - the
    # script is the source of truth for these literal values
    # (e.g. SPICEDB_PASSWORD must match pgvector_init.sql).
    for key, default in STATIC_DEFAULTS.items():
        values.setdefault(key, default)

    rendered = substitute(args.template.read_text(), values, existing_randoms)
    args.output.write_text(rendered)

    # Tight permissions so other users on the host can't read the
    # secrets. Best-effort; ignore failures (e.g. on Windows).
    try:
        os.chmod(args.output, 0o600)
    except OSError:
        pass

    # Render and write garage.toml alongside the rest of the stack.
    # The config itself contains no secrets (those come from .env.prod
    # via env_file in compose), but it lives next to data/garage/{meta,
    # data} so everything garage touches is under one directory.
    GARAGE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    GARAGE_CONFIG_PATH.write_text(GARAGE_CONFIG_TEMPLATE)

    print()
    print(f"wrote {args.output} (mode 600 where supported)")
    print(f"wrote {GARAGE_CONFIG_PATH}")
    print("deploy with:")
    print("  docker compose -f docker-compose.prod.yaml --env-file .env.prod up -d")
    return 0


if __name__ == "__main__":
    sys.exit(main())
