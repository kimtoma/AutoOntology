#!/bin/bash
set -euo pipefail
action="${1:-status}"
target="${2:-all}"
domain="gui/$(id -u)"
case "$action" in start|stop|status) ;; *) echo "Usage: $0 start|stop|status [all|classic|next]" >&2; exit 2;; esac
case "$target" in
 all) names=(autoontology-demo.postgres autoontology-next.postgres autoontology-demo.service autoontology-next.service autoontology-demo.proxy autoontology-demo.tunnel);;
 classic) names=(autoontology-demo.postgres autoontology-demo.service);;
 next) names=(autoontology-next.postgres autoontology-next.service);;
 *) echo "Choose all, classic or next" >&2; exit 2;;
esac
if [[ "$action" == start && "$target" != all ]]; then
 names+=(autoontology-demo.proxy autoontology-demo.tunnel)
fi
if [[ "$action" == stop ]]; then
 reversed=()
 for ((i=${#names[@]}-1; i>=0; i--)); do reversed+=("${names[i]}"); done
 names=("${reversed[@]}")
fi
for name in "${names[@]}"; do
 label="com.kimtoma.$name"
 case "$action" in
  start)
   if ! launchctl print "$domain/$label" >/dev/null 2>&1; then
    launchctl bootstrap "$domain" "$HOME/Library/LaunchAgents/$label.plist"
   fi
   ;;
  stop)
   launchctl bootout "$domain/$label" 2>/dev/null || true
   for ((attempt=0; attempt<225; attempt++)); do
    if ! launchctl print "$domain/$label" >/dev/null 2>&1; then break; fi
    sleep 0.2
   done
   if launchctl print "$domain/$label" >/dev/null 2>&1; then
    echo "Timed out waiting for $label to unload" >&2; exit 1
   fi
   ;;
  status)
   echo "$label"
   launchctl print "$domain/$label" 2>/dev/null | awk '/state =|pid =|last exit code =/' || true
   ;;
 esac
done
