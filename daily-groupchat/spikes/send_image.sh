#!/bin/bash
# Test 1: can a script send an image through Messages? Run on your Mac:
#
#   ./spikes/send_image.sh "+15551234567" path/to/image.png
#
# Use your OWN phone number or Apple ID email, so the image only goes to you.
# The first run asks for permission to control Messages: click OK.
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "usage: $0 <your phone number or Apple ID email> <image.png>" >&2
  exit 1
fi

HANDLE="$1"
SOURCE="$2"

# Messages can be picky about which folders it sends attachments from, so copy the image into ~/Pictures first.
mkdir -p "$HOME/Pictures/daily-groupchat"
FILE="$HOME/Pictures/daily-groupchat/$(basename "$SOURCE")"
cp "$SOURCE" "$FILE"

osascript - "$HANDLE" "$FILE" <<'APPLESCRIPT'
on run argv
  set targetHandle to item 1 of argv
  set imagePath to item 2 of argv
  tell application "Messages"
    set iMessageService to 1st account whose service type = iMessage
    set me_ to participant targetHandle of iMessageService
    send "🗞️ [bot] test: an image should arrive right after this text" to me_
    send (POSIX file imagePath) to me_
  end tell
end run
APPLESCRIPT

echo "Sent. Check your phone: you should see the text AND the image."
echo "If only the text arrived, note any error above and tell Claude. That decides how drafts get delivered."
