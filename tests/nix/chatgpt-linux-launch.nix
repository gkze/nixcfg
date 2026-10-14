# End-to-end launch check for the Linux ChatGPT desktop app.
#
# The Linux package is repacked from OpenAI's Debian artifact, so nothing short
# of starting the Electron app proves the bundle is usable. This runs the
# installed wrapper unchanged — no extra sandbox or GPU flags — under a private
# Xvfb display, and fails unless the app presents a window and survives holding
# it, so a wrapper regression fails here rather than on a user's machine.
{
  lib,
  runCommand,
  xvfb,
  xdotool,
  procps,
  coreutils,
  chatgpt,
}:

runCommand "chatgpt-linux-launch-check"
  {
    nativeBuildInputs = [
      xvfb
      xdotool
      procps
      coreutils
    ];
    meta = {
      description = "Launch the ChatGPT Linux app under Xvfb and assert its window appears";
      platforms = lib.platforms.linux;
    };
  }
  ''
    for path in "${chatgpt}/bin/chatgpt" "${chatgpt}/share/applications/chatgpt.desktop"; do
      if [ ! -e "$path" ]; then
        echo "missing required runtime path: $path" >&2
        exit 1
      fi
    done

    export HOME="$TMPDIR/home"
    mkdir -p "$HOME"
    export CHATGPT_APP="${chatgpt}/bin/chatgpt"

    cat > "$TMPDIR/launch.sh" <<'SCRIPT'
    set -uo pipefail

    display=:99
    Xvfb "$display" -screen 0 1280x1024x24 -nolisten tcp >"$TMPDIR/xvfb.log" 2>&1 &
    xvfb_pid=$!
    export DISPLAY="$display"

    ready=""
    for _ in $(seq 1 30); do
      if xdotool getdisplaygeometry >/dev/null 2>&1; then
        ready=yes
        break
      fi
      sleep 1
    done
    if [ -z "$ready" ]; then
      echo "FAIL: Xvfb did not come up" >&2
      cat "$TMPDIR/xvfb.log" >&2 || true
      exit 1
    fi

    app_log="$TMPDIR/app.log"
    "$CHATGPT_APP" \
      --disable-gpu \
      --disable-dev-shm-usage \
      --user-data-dir="$HOME/.config/chatgpt-launch-check" \
      >"$app_log" 2>&1 &
    app_pid=$!

    found=""
    for i in $(seq 1 90); do
      if xdotool search --onlyvisible --name 'ChatGPT' >/dev/null 2>&1; then
        found=yes
        echo "window appeared after ''${i}s"
        break
      fi
      if ! kill -0 "$app_pid" 2>/dev/null; then
        echo "app process exited before presenting a window" >&2
        break
      fi
      sleep 1
    done

    echo "--- visible windows ---"
    for wid in $(xdotool search --onlyvisible --name '.*' 2>/dev/null); do
      printf 'window %s name=%s\n' "$wid" "$(xdotool getwindowname "$wid" 2>/dev/null)"
    done

    if [ -n "$found" ]; then
      sleep 5
      if kill -0 "$app_pid" 2>/dev/null; then
        echo "app still alive after presenting its window"
      else
        echo "app died within 5s of showing a window" >&2
        found=""
      fi
    fi

    kill "$app_pid" 2>/dev/null || true
    wait "$app_pid" 2>/dev/null || true
    kill "$xvfb_pid" 2>/dev/null || true

    if [ -z "$found" ]; then
      echo "--- app log (tail) ---"
      tail -40 "$app_log" || true
      echo "FAIL: ChatGPT never presented a window" >&2
      exit 1
    fi
    echo "PASS: ChatGPT presented a stable window"
    SCRIPT

    bash "$TMPDIR/launch.sh"

    mkdir -p "$out"
    echo "chatgpt linux launch check passed" > "$out/result"
  ''
