#!/bin/sh
# Production's Alertmanager starts here (infra/docker-compose.prod.yml; README, Alerting). The
# Discord webhook URL and the SMTP password from .env.prod go to the files alertmanager.yml names,
# on tmpfs and readable by Alertmanager's user alone; the two addresses go over its placeholders in
# a copy on /tmp; then the command runs without the four in its environment. A missing or malformed
# setting stops it here, naming the setting. Nothing here prints a value.
set -eu

for name in ALERT_DISCORD_WEBHOOK_URL ALERT_SMTP_PASSWORD ALERT_EMAIL_FROM ALERT_EMAIL_TO; do
  eval "value=\${$name:-}"
  if [ -z "$value" ]; then
    echo "alertmanager: $name is empty: set it in .env.prod (README, Alerting)" >&2
    exit 1
  fi
done
# The proxy lets Alertmanager reach discord.com alone, so a discordapp.com, canary. or ptb. webhook
# would fail at the first alert instead of here.
case "$ALERT_DISCORD_WEBHOOK_URL" in
  https://discord.com/api/webhooks/*) ;;
  *)
    echo "alertmanager: ALERT_DISCORD_WEBHOOK_URL must start with https://discord.com/api/webhooks/" >&2
    exit 1
    ;;
esac
# Google shows an app password in groups of four letters; Gmail takes the 16 letters together.
case "$ALERT_SMTP_PASSWORD" in
  *" "*)
    echo "alertmanager: ALERT_SMTP_PASSWORD has a space: paste the 16 letters without spaces" >&2
    exit 1
    ;;
esac
# The addresses go into the config as written, so only an address's own characters pass: one
# sender, and recipients as a comma-separated list.
case "$ALERT_EMAIL_FROM" in
  *[!A-Za-z0-9.@+_-]*)
    echo "alertmanager: ALERT_EMAIL_FROM takes one address: letters, digits and . @ + _ - only" >&2
    exit 1
    ;;
esac
case "$ALERT_EMAIL_TO" in
  *[!A-Za-z0-9.@+_,-]*)
    echo "alertmanager: ALERT_EMAIL_TO takes addresses: letters, digits and . @ + _ - , only" >&2
    exit 1
    ;;
esac

umask 077
printf '%s' "$ALERT_DISCORD_WEBHOOK_URL" > /tmp/discord-webhook-url
printf '%s' "$ALERT_SMTP_PASSWORD" > /tmp/smtp-password
sed -e "s/ALERT_EMAIL_FROM/$ALERT_EMAIL_FROM/g" -e "s/ALERT_EMAIL_TO/$ALERT_EMAIL_TO/g" \
  /etc/alertmanager/alertmanager.yml > /tmp/alertmanager.yml
unset value ALERT_DISCORD_WEBHOOK_URL ALERT_SMTP_PASSWORD ALERT_EMAIL_FROM ALERT_EMAIL_TO
exec "$@"
