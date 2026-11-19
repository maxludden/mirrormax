#!/bin/zsh
# Trimmed copy of the legacy script, used to test --import-legacy.
DP="/opt/homebrew/bin/displayplacer"
STATE="$HOME/.vnc-mirror-state"
MIRROR_ARGS=("id:37D8832A-2D66-02CA-B9F7-8F30A301B230+6B26DAE3-CCEA-48CF-B064-462B0F753F39 res:1470x956 hz:60 color_depth:8 enabled:true scaling:on origin:(0,0) degree:0")
NORMAL_ARGS=("id:37D8832A-2D66-02CA-B9F7-8F30A301B230 res:1280x832 hz:60 color_depth:8 enabled:true scaling:on origin:(0,0) degree:0" "id:6B26DAE3-CCEA-48CF-B064-462B0F753F39 res:1920x1080 hz:60 color_depth:8 enabled:true scaling:off origin:(-359,-1080) degree:0")
last_event=$(grep -E 'SServerAgent: (Starting|Stopping) desktop' "$LOG" 2>/dev/null | tail -1)
