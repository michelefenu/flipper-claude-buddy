# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

### Flipper App (C)
```bash
# Build (requires ufbt: pip3 install ufbt)
cd flipper-app && ufbt build

# Build and flash to connected Flipper
cd flipper-app && ufbt launch

# Or use the script
./scripts/build-flipper.sh           # build only
./scripts/build-flipper.sh --flash   # build + flash
```

New `.c` files must be added to `sources=[...]` in `flipper-app/application.fam`.

**Exit the app on the Flipper (hold Back) before `ufbt launch`.** While the app runs over USB, `serial.c` detaches the CLI (`cli_vcp_disable`) and takes the only CDC channel, so `ufbt launch` hangs right after "Using flip_…". The CLI comes back when the app exits (`cli_vcp_enable` in `usb_stop`). Don't remove that pairing: without it the CLI stays dead until a reboot. If an interrupted launch leaves the CLI unresponsive, unplugging USB isn't enough: reboot the Flipper (hold Left + Back).

**Also stop the bridge first** (close every Claude Code session running the plugin). Once the app exits, the bridge's USB reconnect loop opens the last `/dev/cu.usbmodem*` port (`transport_usb.py`), which is now the CLI. `ufbt launch` then fails with "multiple access on port".

The fork's app id is `claudagotchi` (display name "Claudagotchi", data in `/ext/apps_data/claudagotchi/`), so it installs next to the upstream `claude_buddy` app without touching it.

### Host Bridge (Python ≥ 3.10)
```bash
# Install (editable)
cd plugin/host-bridge && pip3 install -e .

# Run bridge daemon
python3 -m bridge

# Run with explicit transport
python3 -m bridge --transport ble   # BLE only
python3 -m bridge --transport usb   # USB only
```

Environment overrides (all read in `bridge/config.py` / `__main__.py`):
```bash
FLIPPER_TRANSPORT=auto|usb|ble      # default auto: USB first, then BLE
FLIPPER_SERIAL_PORT=/dev/cu.usbmodemXXX
FLIPPER_BT_NAME="Flipper"           # name fallback; primary BLE scan filter is service UUID 0x3082
FLIPPER_BT_SCAN_TIMEOUT=10
FLIPPER_LOG_LEVEL=debug
FLIPPER_BRIDGE_SOCKET=/tmp/claude-flipper-bridge.sock
FLIPPER_DICTATION_BACKEND=macos|custom|none   # + FLIPPER_DICTATION_{START,STOP,CHECK}_CMD for custom
```
Plugin `userConfig` (`serial_port`, `transport`, `bluetoothName`) is forwarded to these env vars by `on-session-start.sh`.

### Testing
There is no test suite or linter. CI (`.github/workflows/build-fap.yml`) only builds the FAP. Verify changes by building, flashing, and watching the bridge log. To poke the bridge's IPC socket by hand:
```bash
echo '{"action":"notify","sound":"success","vibro":true,"text":"Test","subtext":""}' \
  | nc -U /tmp/claude-flipper-bridge.sock
```
IPC actions handled in `Daemon` (`daemon.py`): `notify`, `display`, `claude_connect`, `claude_disconnect`, `register_target`, `release_target`, `permission_request`.

### Debug scripts (`scripts/`)
- `flipper-log.py` — tail the Flipper's serial CLI log (`--debug` enables debug logging on device)
- `scan-nus.py` — BLE scan showing what Claude Desktop's Hardware Buddy picker sees
- `refresh-name.py` — force macOS to refresh its cached BLE device name

## Architecture

The Flipper app has two modes, persisted in `/ext/apps_data/claudagotchi/settings.bin` (`app_settings.c`, `BleMode`) and toggled from the on-device info menu:

```
Bridge mode (BleModeBridge, default)            Desktop mode (BleModeDesktop)
Flipper Zero (C app)                            Flipper Zero (C app)
  ↕ USB CDC  OR  BLE serial (custom UUIDs)        ↕ BLE Nordic UART Service (advertises "Claude")
Host Bridge (Python daemon)                     Claude Desktop app (Anthropic Hardware Buddy protocol)
  ↕ Unix socket /tmp/claude-flipper-bridge.sock   — no plugin, no bridge
Claude Code hook scripts (plugin/scripts/)
```

1. **`flipper-app/`**: the Flipper Zero FAP (C). Handles button input and audio/haptic/LED feedback. At startup and on mode toggle, `claude_buddy.c` picks the transport: USB if the Flipper is on USB power *and* in Bridge mode, otherwise BLE (`transport_bt.c` for Bridge, `transport_nus.c` for Desktop). All transports implement the vtable in `transport.h`. The `nus_*.c` files belong to Desktop mode only.

2. **`plugin/host-bridge/`**: the Python asyncio daemon. Bridges serial ↔ Unix socket and translates Flipper button events into keystrokes for the terminal (`input.py`: osascript on macOS, xdotool on Linux) and dictation (`voice.py`).

3. **`plugin/`**: the Claude Code plugin. `hooks/hooks.json` maps each lifecycle event to a script in `scripts/`. The plugin is published through the root `.claude-plugin/marketplace.json` (a git-subdir pointing at `plugin/`).

### Bridge lifecycle (managed by hooks, not by hand)
- `on-session-start.sh` creates a venv at `$CLAUDE_PLUGIN_DATA/venv` and installs the bridge into it. It hashes `pyproject.toml` + `bridge/*.py`; **when the hash changes, it reinstalls and kills/restarts a running daemon**. So bridge code edits take effect on the next session start.
- Sessions are refcounted in `/tmp/claude-flipper-bridge.refcount`. `on-session-end.sh` stops the daemon when the count reaches 0, except when the reason is `clear` or `resume`. In those cases the same Claude process immediately starts a new session, so killing the bridge would race its SessionStart: the start hook sees the dying bridge's socket and skips the restart, which leaves the Flipper stuck on "Session End".
- `session-target.py` registers the current terminal app/tab (`register_target`) so keystrokes go to the right window when several sessions are open.

### Permission round-trip
`on-permission-request.py` sends `permission_request` over the socket and **blocks** while the bridge sends `perm` to the Flipper and waits up to 60s for `perm_resp`. On `no_flipper` / `timeout` / `busy` / `ask`, the hook falls back to Claude Code's normal permission dialog. Only one permission request can be in flight at a time.

`AskUserQuestion` also fires `PermissionRequest`, but it's a multiple-choice picker, not Allow/Deny. The hook skips the Flipper permission screen for it, sends an alert `notify` with the first question's text, and exits without a decision so Claude Code shows its own picker. That picker is driven from the Flipper's main screen: Up/Down are arrows, OK is Enter, Left is Esc.

## Threading Model — Critical

### Flipper App
- **RX callbacks of every transport** (USB, BT, NUS) run on a worker thread. They must NOT call any UI functions or `transport_send`.
- The shared callback `on_serial_data` in `claude_buddy.c` parses the line (`protocol_parse`, or `nus_protocol_parse` in Desktop mode), queues it into a `FuriMessageQueue`, and signals the GUI thread via `view_dispatcher_send_custom_event`.
- **GUI thread** (the Furi event loop) drains the queue and calls `transport_send` safely.
- Calling `ble_profile_serial_tx` from inside `bt_serial_event_cb` deadlocks on Momentum firmware. Always defer TX to the GUI thread.

### Host Bridge
- Single asyncio event loop. All transport I/O, IPC, and ping tasks are async coroutines.
- `serial_conn.py` runs a reconnect loop that re-establishes the transport on disconnect.
- `transport_bt.py`: `readline()` must handle disconnect without blocking. It checks the `_closed` flag before and after `_rx_event.clear()`.
- `claude_ipc.py` reads each request with a single `read(65536)`, not until EOF: shell hooks use `nc`, which doesn't half-close the socket. Large payloads can arrive split and fail to parse, so keep hook payloads small. Hooks cap subtexts at 63 chars (`[:63]`), matching `SUBTEXT_MAX` in `protocol.py` and the Flipper's 64-byte field, which the status screen wraps over up to 3 lines.

## Protocol (Bridge mode)

JSON lines (`\n`-terminated) over serial (USB or BLE):
```json
{"v": 1, "t": "<type>", "d": {...}}
```

**Host → Flipper** (`bridge/protocol.py`): `ping`, `notify`, `state`, `status`, `menu`, `perm`
**Flipper → Host** (`protocol_build_*` in `protocol.h`): `hello`, `pong`, `perm_resp`, `cmd`, plus one message per key: `enter`, `esc`, `up`, `down`, `voice` (macOS dictation toggle; no button sends it since Up-tap became `up`), `tab`, `yes` (types "yes" + Enter; unbound since OK-hold became `tab`), `interrupt` (Ctrl+C; unbound since Left-hold became `/clear`), `backspace`, `space_down`/`space_up`, `pgup`/`pgdown`, `ctrl_o`, `ctrl_e`, `shift_tab`

Adding a button event touches all three layers: a builder in `protocol.c/.h`, the sender in `ui.c`/`claude_buddy.c`, and a `msg_type` branch in `Daemon` (`daemon.py`) that calls the input backend. New key names must be added to `_key_code` in `input.py`: on macOS an unknown name silently falls back to Return (key code 36).

The Flipper sends `hello` on the first received `ping` (from the GUI thread), not at BLE connect time. This is because the host's CCCD write (enabling notifications) hasn't happened yet when the connection status callback fires. `hello_sent` is reset on every transport change and reconnect.

Desktop mode uses Anthropic's Hardware Buddy wire protocol instead (`nus_protocol.c`). It is unrelated to the JSON protocol above.

## BLE Transport Details (Bridge mode)

- RX (Flipper→host, notify): `19ed82ae-ed21-4c9d-4145-228e61fe0000`
- TX (host→Flipper, write): `19ed82ae-ed21-4c9d-4145-228e62fe0000`
- Host writes with `response=False` (write-without-response), chunk size capped to `negotiated_mtu - 3`
- `BT_WRITE_CHUNK = 128` in `config.py` (runtime cap applies)

## Key Files

| File | Purpose |
|------|---------|
| `flipper-app/claude_buddy.c` | App entry point, GUI event loop, transport selection, message dispatch |
| `flipper-app/transport_bt.c` / `serial.c` / `transport_nus.c` | BLE (Bridge) / USB CDC / BLE NUS (Desktop) transports |
| `flipper-app/ui.c` | Display rendering, button input handlers, About page version |
| `flipper-app/protocol.c` | JSON parse/build for Bridge-mode messages |
| `flipper-app/nus_*.c` | Desktop mode: NUS GATT profile, Hardware Buddy protocol, state machine, transcript, stats, character packs |
| `plugin/host-bridge/bridge/daemon.py` | Main event loop, Flipper message routing, IPC handlers, command-menu discovery |
| `plugin/host-bridge/bridge/input.py` | Keystroke backends (osascript / xdotool / no-op) |
| `plugin/host-bridge/bridge/transport_bt.py` | BLE transport (bleak), readline, write |
| `plugin/host-bridge/bridge/serial_conn.py` | Reconnect loop, disconnect detection |
| `plugin/host-bridge/bridge/config.py` | All tunables (timeouts, UUIDs, chunk sizes, env vars) |
| `plugin/hooks/hooks.json` + `plugin/scripts/` | Hook wiring and one script per Claude Code lifecycle event |

## Runtime Files
- Socket: `/tmp/claude-flipper-bridge.sock`
- PID: `/tmp/claude-flipper-bridge.pid`
- Log: `/tmp/claude-flipper-bridge.log`
- Session refcount: `/tmp/claude-flipper-bridge.refcount`
- Turn stats: `/tmp/claude-flipper-turn-stats.json`: tool usage counts written by `on-post-tool-use.py`, read by `on-stop.sh`
- Skip-stop flag: `/tmp/claude-flipper-skip-stop.flag`: set by hook Bash commands that write directly to the socket, so `on-stop.sh` doesn't notify twice
- Plugin data (`$CLAUDE_PLUGIN_DATA`, fallback `/tmp/flipper-claude-buddy`): `venv/` and `bt_name`, the Bluetooth name auto-detected from the first `hello` and reused across sessions to skip re-scanning

To inspect bridge activity: `tail -f /tmp/claude-flipper-bridge.log`

## Platform Notes

| Feature | macOS | Linux |
|---------|-------|-------|
| USB transport | `/dev/cu.usbmodem*` | `/dev/ttyACM*` (auto-detected; may need `dialout` group) |
| BLE transport | ✓ | ✓ via BlueZ (bleak) |
| Keystroke forwarding | AppleScript (`osascript`, needs Accessibility permission for the terminal) | `xdotool` (X11 only; install via `apt install xdotool`) |
| Wayland keystroke | ✗ | not yet supported (`ydotool` needed) |
| Dictation | macOS native (`FLIPPER_DICTATION_BACKEND=macos`) | disabled by default; use `FLIPPER_DICTATION_BACKEND=custom` |

On Linux, `WINDOWID` (set by VTE-based terminals like gnome-terminal and kitty) is used by `xdotool` to focus the correct window. If `WINDOWID` is not set, keystrokes go to the active window.

## Command Menu System

`Daemon._load_commands` (`daemon.py`) builds the Flipper's slash-command menu by merging these sources:
1. Built-in Claude Code commands (`BUILTIN_COMMANDS`)
2. `commands/*.md` and `skills/*/SKILL.md` under both `~/.claude/` and `$FLIPPER_PROJECT_DIR/.claude/`
3. Commands and skills of enabled plugins (`enabledPlugins` in settings.json, resolved under `~/.claude/plugins/`)
4. Legacy `~/.claude/flipper-commands.txt` and `$FLIPPER_PROJECT_DIR/.claude/flipper-commands.txt`: one command per line, `#` comments

Items are truncated to 26 chars and sent as a `|`-joined `menu` message. The bridge keeps `_cmd_map` (truncated → full command) and expands the `cmd` the Flipper sends back.

## Releasing a New Version

1. **Commit any uncommitted changes first.** The version bump should be its own clean commit.
2. **`flipper-app/CHANGELOG.md`**: add a new `## vX.Y` section at the top, summarizing commits since the previous version.
3. **`flipper-app/application.fam`**: update `fap_version`
4. **`flipper-app/ui.c`**: update the version string on the About page
5. **`plugin/.claude-plugin/plugin.json`**: update `version`
6. **`plugin/host-bridge/pyproject.toml`**: update `version`
7. Commit, push, then tag:
   ```bash
   git tag X.Y
   git push origin X.Y
   ```
   The CI workflow (`.github/workflows/build-fap.yml`) creates the GitHub release and attaches the built `.fap` automatically.
