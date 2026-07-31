#!/usr/bin/env bash
set -euo pipefail

REPO="/c/Users/paulz/Documents/GitHub/i-will-find-it"
TEMPLATE="$REPO/.env.prod.default"
OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT

cp "$TEMPLATE" "$OUT"

declare -A values=(
    [IMAGE_TAG]="v0.1.0"
    [DOMAIN]="inu-the-bot.com"
    [FRONTEND_HOST]="inu-the-bot.com"
    [ACME_EMAIL]="ops@example.com"
    [POSTGRES_USER]="postgres"
    [POSTGRES_PASSWORD]="hunter2"
    [POSTGRES_DB]="db"
    [GARAGE_DEFAULT_ACCESS_KEY]="AKIA"
    [GARAGE_DEFAULT_SECRET_KEY]="secret"
    [GARAGE_DEFAULT_BUCKET]="garage"
    [DISCORD_CLIENT_ID]="12345"
    [DISCORD_CLIENT_SECRET]="shh"
)
declare -A randoms=(
    [SPICEDB_PASSWORD]="spicedb-pw-hex"
    [GRPC_SPICEDB_CREDENTIALS]="spicedb-key-hex"
    [JWT_SECRET]="jwt-hex"
    [SESSION_SECRET]="session-hex"
)

for key in "${!randoms[@]}"; do
    val="${randoms[$key]}"
    esc_val="$(printf '%s' "$val" | sed -e 's/[\\/&]/\\&/g')"
    sed -i -E "s|@random:${key}@|${esc_val}|g" "$OUT"
done

for key in "${!values[@]}"; do
    val="${values[$key]}"
    esc_val="$(printf '%s' "$val" | sed -e 's/[\\/&]/\\&/g')"
    sed -i -E "s|@ask:${key}@|${esc_val}|g" "$OUT"
done

if grep -qE '@(ask|random):[A-Z_]+@' "$OUT"; then
    echo "FAIL: leftover placeholders:" >&2
    grep -nE '@(ask|random):[A-Z_]+@' "$OUT" >&2
    exit 1
fi

grep -q '^IMAGE_TAG=v0.1.0$' "$OUT" || { echo "FAIL IMAGE_TAG"; exit 1; }
grep -q '^DOMAIN=inu-the-bot.com$' "$OUT" || { echo "FAIL DOMAIN"; exit 1; }
grep -q '^ACME_EMAIL=ops@example.com$' "$OUT" || { echo "FAIL ACME_EMAIL"; exit 1; }
grep -q '^POSTGRES_PASSWORD=hunter2$' "$OUT" || { echo "FAIL PG_PW"; exit 1; }
grep -q '^GARAGE_DEFAULT_ACCESS_KEY=AKIA$' "$OUT" || { echo "FAIL GARAGE_A"; exit 1; }
grep -q '^DISCORD_CLIENT_SECRET=shh$' "$OUT" || { echo "FAIL DISCORD_S"; exit 1; }
grep -q '^SPICEDB_PASSWORD=spicedb-pw-hex$' "$OUT" || { echo "FAIL SPICEDB_PW"; exit 1; }
grep -q '^GRPC_SPICEDB_CREDENTIALS=spicedb-key-hex$' "$OUT" || { echo "FAIL SPICEDB_KEY"; exit 1; }
grep -q '^JWT_SECRET=jwt-hex$' "$OUT" || { echo "FAIL JWT"; exit 1; }
grep -q '^SESSION_SECRET=session-hex$' "$OUT" || { echo "FAIL SESSION"; exit 1; }

grep -q '^# Production env for WerSu\.$' "$OUT" || { echo "FAIL header comment"; exit 1; }
grep -q '^# Apex domain under which' "$OUT" || { echo "FAIL block comment"; exit 1; }

grep -q '^DISCORD_REDIRECT_URI=https://api.\${DOMAIN}/api/auth/discord/callback$' "$OUT" || { echo "FAIL DERIVED1"; exit 1; }
grep -q '^FRONTEND_URL=https://\${FRONTEND_HOST}$' "$OUT" || { echo "FAIL DERIVED2"; exit 1; }
grep -q '^BACKEND_URL=https://api.\${DOMAIN}$' "$OUT" || { echo "FAIL DERIVED3"; exit 1; }
grep -q '^IMGPROXY_ADDRESS=http://imgproxy:8080$' "$OUT" || { echo "FAIL IMGPROXY"; exit 1; }

echo "SMOKE OK"
echo "--- generated output ---"
cat "$OUT"
