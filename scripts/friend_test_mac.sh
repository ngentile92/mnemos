#!/usr/bin/env bash
# End-to-end "friend test" on a clean Mac: install from the .dmg, first-run setup, import, save, duplicate
# warning, Connect to Claude Desktop, and Claude's stdio launch. Screenshots go to $SHOTS (for the install guide).
#   scripts/friend_test_mac.sh path/to/Mnemos-macos.dmg [shots-dir]
# Meant for CI runners or a throwaway user: it installs into /Applications and writes a LaunchAgent.
set -euo pipefail
DMG="$1"; SHOTS="${2:-$PWD/shots}"; mkdir -p "$SHOTS"
T="$(mktemp -d)"; export MNEMOS_HOME="$T/Mnemos" MNEMOS_CLAUDE_CONFIG="$T/claude/claude_desktop_config.json"
APP=/Applications/Mnemos.app; BIN="$APP/Contents/MacOS/mnemos-desktop"; PORT=8765
step() { printf '\n== %s\n' "$*"; }
fail() { echo "FRIEND TEST FAILED: $*"; cat "$MNEMOS_HOME/hub.log" 2>/dev/null | tail -30 || true; exit 1; }
to() { local s="$1"; shift; perl -e 'alarm shift; exec @ARGV' "$s" "$@"; }   # timeout without coreutils
shot() { to 15 screencapture -x "$SHOTS/$1.png" 2>/dev/null || echo "(no screenshot: $1)"; }

step "Keychain usable without a prompt?"
if [ -z "${MNEMOS_NO_KEYCHAIN:-}" ] && ! to 15 security add-generic-password -U -s io.mnemos.probe -a probe -w probe 2>/dev/null; then
  echo "Keychain not usable here (locked / would prompt): testing with the token file instead"; export MNEMOS_NO_KEYCHAIN=1
fi
security delete-generic-password -s io.mnemos.probe -a probe >/dev/null 2>&1 || true

step "install from the dmg (drag to Applications)"
MNT="$(hdiutil attach -nobrowse -readonly "$DMG" | awk -F'\t' '/\/Volumes\//{print $NF}' | tail -1)"
rm -rf "$APP"; cp -R "$MNT/Mnemos.app" /Applications/; hdiutil detach -quiet "$MNT"
xattr -cr "$APP"   # what right-click → Open grants for an unsigned app
[ -x "$BIN" ] || BIN="$(ls -d "$APP"/Contents/MacOS/* | head -1)"

step "first-run wizard (same code as the Create button)"
echo "friend-pass-123" | to 120 "$BIN" --setup personal,work Ana || fail "setup"
[ -f "$MNEMOS_HOME/contexts.yaml" ] || fail "no contexts.yaml"
[ -f "$HOME/Library/LaunchAgents/io.mnemos.desktop.plist" ] || fail "start-at-login not set"
if [ "${MNEMOS_NO_KEYCHAIN:-}" != 1 ]; then
  [ ! -f "$MNEMOS_HOME/token" ] || fail "token still on disk (should be in the Keychain)"
  # only Mnemos itself may read the item (the `security` CLI would get an access prompt); the hub starting
  # below and --open-memory both prove the app reads it back
  echo "token moved to the Keychain"
fi

step "open the app (hub starts by itself)"
for _ in $(seq 15); do curl -fs "http://127.0.0.1:$PORT/" >/dev/null || break; sleep 1; done
curl -fs "http://127.0.0.1:$PORT/" >/dev/null && fail "port $PORT busy before start (a hub outlived its app)"
"$BIN" > "$T/app.log" 2>&1 & APP_PID=$!
for _ in $(seq 60); do curl -fs "http://127.0.0.1:$PORT/" >/dev/null && break; sleep 2; done
curl -fs "http://127.0.0.1:$PORT/" | grep -q "Mnemos is running" || { cat "$T/app.log"; fail "hub did not start"; }
sleep 3; shot 1-app-window

step "log into the memory screen with the password"
J=(-H "Content-Type: application/json" -H "X-Mnemos-Write: 1" -b "$T/c" -c "$T/c")
curl -fs "${J[@]}" -X POST "http://127.0.0.1:$PORT/api/login" -d '{"password":"friend-pass-123"}' >/dev/null || fail "login"
api() { curl -fsS "${J[@]}" -X POST "http://127.0.0.1:$PORT/api/$1" -d "$2"; }

step "import what Claude knows"
PREV="$(api import/preview '{"context":"personal","source":"claude","text":"- Ana lives in Lisbon\n- Ana has two cats called Mia and Leo\n- Ana works as a nurse"}')"
echo "$PREV" | head -c 600; echo
NOTES="$(echo "$PREV" | /usr/bin/python3 -c 'import json,sys;d=json.load(sys.stdin);print(json.dumps([c["text"] for c in d["items"] if not c["duplicate"]]))')"
api import/commit "{\"context\":\"personal\",\"source\":\"claude\",\"notes\":$NOTES}" | grep -q '"saved": *3' || fail "import did not save 3 notes"

step "save a note, then the same note again → duplicate warning"
api memory/save '{"context":"personal","text":"Ana prefers green tea in the morning"}' | grep -qv duplicate || fail "first save"
api memory/save '{"context":"personal","text":"Ana prefers green tea in the morning."}' | grep -q '"duplicate"' || fail "no duplicate warning"
N="$(curl -fs -b "$T/c" "http://127.0.0.1:$PORT/api/memory/list?context=personal&include_shared=0" | /usr/bin/python3 -c 'import json,sys;print(len(json.load(sys.stdin)["items"]))')"
[ "$N" = 4 ] || fail "expected 4 notes, got $N"

step "memory screen screenshots"
to 30 "$BIN" --open-memory || fail "open my memory (token from Keychain)"; sleep 20; shot 2-memory

step "Connect to Claude Desktop (same code as the button)"
to 30 "$BIN" --connect-claude personal || fail "connect"
CMD="$(/usr/bin/python3 -c 'import json,os;s=json.load(open(os.environ["MNEMOS_CLAUDE_CONFIG"]))["mcpServers"]["mnemos-personal"];print(json.dumps([s["command"]]+s["args"]))')"
echo "$CMD"
echo "$CMD" | grep -q "/Applications/Mnemos.app/Contents/Resources/python/bin/python3" || fail "Claude config does not point at the app"

step "launch it like Claude Desktop does (stdio) and list tools"
to 120 /usr/bin/python3 - "$CMD" <<'PY' || fail "stdio server did not answer"
import json, subprocess, sys
cmd = json.loads(sys.argv[1])
p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
def send(m): p.stdin.write(json.dumps(m) + "\n"); p.stdin.flush()
send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "friend-test", "version": "1"}}})
assert "serverInfo" in p.stdout.readline()
send({"jsonrpc": "2.0", "method": "notifications/initialized"})
send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_search", "arguments": {"query": "cats", "mode": "keyword"}}})
out = p.stdout.readline(); p.kill()
assert "Mia" in out, out[:400]
print("stdio OK: Claude would find the imported note")
PY

step "quit the app (Cmd+Q) → the hub stops too"
to 20 osascript -e 'tell application "Mnemos" to quit' 2>/dev/null || kill "$APP_PID" 2>/dev/null || true
for _ in $(seq 10); do curl -fs "http://127.0.0.1:$PORT/" >/dev/null || break; sleep 1; done
curl -fs "http://127.0.0.1:$PORT/" >/dev/null && fail "hub left running after quitting the app" || true
rm -f "$HOME/Library/LaunchAgents/io.mnemos.desktop.plist"
echo; echo "FRIEND TEST PASSED"
