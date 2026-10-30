#!/bin/bash
set -euo pipefail
domain="gui/$(id -u)"
case "${1:-status}" in
 start)
  for name in postgres service backend proxy tunnel; do
   label="com.kimtoma.autoontology-demo.$name"
   if ! launchctl print "$domain/$label" >/dev/null 2>&1; then
    launchctl bootstrap "$domain" "$HOME/Library/LaunchAgents/$label.plist"
   fi
  done
  ;;
 stop)
  for name in tunnel proxy backend service postgres; do
   launchctl bootout "$domain/com.kimtoma.autoontology-demo.$name" 2>/dev/null || true
  done
  ;;
 status)
  for name in postgres service backend proxy tunnel; do
   label="com.kimtoma.autoontology-demo.$name"
   echo "$label"
   launchctl print "$domain/$label" 2>/dev/null | awk '/state =|pid =|last exit code =/' || true
  done
  ;;
 *) echo "Usage: $0 start|stop|status"; exit 2 ;;
esac
