#!/bin/bash
set -euo pipefail
domain="gui/$(id -u)"
case "${1:-status}" in
 start)
  for name in postgres service proxy tunnel; do
   label="com.kimtoma.autoontology-demo.$name"
   if ! launchctl print "$domain/$label" >/dev/null 2>&1; then
    launchctl bootstrap "$domain" "$HOME/Library/LaunchAgents/$label.plist"
   fi
  done
  ;;
 stop)
  for name in tunnel proxy backend service postgres; do
   label="com.kimtoma.autoontology-demo.$name"
   launchctl bootout "$domain/$label" 2>/dev/null || true
   for ((attempt=0; attempt<150; attempt++)); do
    if ! launchctl print "$domain/$label" >/dev/null 2>&1; then break; fi
    sleep 0.2
   done
   if launchctl print "$domain/$label" >/dev/null 2>&1; then
    echo "Timed out waiting for $label to unload" >&2
    exit 1
   fi
  done
  ;;
 status)
  for name in postgres service proxy tunnel; do
   label="com.kimtoma.autoontology-demo.$name"
   echo "$label"
   launchctl print "$domain/$label" 2>/dev/null | awk '/state =|pid =|last exit code =/' || true
  done
  ;;
 *) echo "Usage: $0 start|stop|status"; exit 2 ;;
esac
