#!/usr/bin/env bash
# One-step setup of a PS5 DualSense controller on this computer: checks the prerequisites,
# pairs and connects the controller over Bluetooth, and verifies that Python can read it.
#
#   ./setup.sh             connect the controller; pairs it if this computer does not know it yet
#   ./setup.sh --repair    delete this computer's pairing with the controller, then pair again
#
# A controller on a USB data cable needs no pairing: the script then only verifies it.
# Needs no root rights. The programs run on the system Python, because that is the one with GTK 3,
# which the game window is drawn with; a desktop Linux such as Ubuntu has both.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PYTHON=/usr/bin/python3

SCAN_SECONDS=120        # wait this long for the controller to enter pairing mode
RECONNECT_SECONDS=30    # wait this long for an already paired controller to reconnect
READY_SECONDS=20        # wait this long for the input device once Bluetooth is connected
USB_HINT="也可以用 USB 数据线连接手柄，然后重新运行本脚本。"

SCAN_PID=""             # background scan, stopped on exit
RESTORE_PAIRABLE=0      # 1 if the adapter must be made non-pairable again on exit
ADAPTER=""              # output of `bluetoothctl show`
KNOWN=""                # addresses of the DualSense controllers BlueZ has a record of, one per line
MAC=""                  # address of the controller found by the scan

die()  { echo "错误: $*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }
bt()   { timeout 40 bluetoothctl "$@"; }

cleanup() {
    if [ -n "$SCAN_PID" ]; then kill "$SCAN_PID" 2>/dev/null || true; fi
    if [ "$RESTORE_PAIRABLE" = 1 ]; then bt pairable off >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

# wait_until SECONDS COMMAND...: run COMMAND once per second until it succeeds.
wait_until() {
    local deadline=$((SECONDS + $1))
    shift
    until "$@"; do
        [ "$SECONDS" -lt "$deadline" ] || return 1
        sleep 1
    done
}

# Is kernel module $1 loaded, built in, or installed for the running kernel?
has_module() {
    local dir
    dir="/lib/modules/$(uname -r)"
    [ -d "/sys/module/${1//-/_}" ] || grep -qs "/$1\.ko" "$dir/modules.dep" "$dir/modules.builtin"
}

controller_ready() { "$PYTHON" dualsense.py >/dev/null 2>&1; }

require_bluetooth() {
    command -v bluetoothctl >/dev/null || die "找不到 bluetoothctl（软件包 bluez）。$USB_HINT"
    ADAPTER=$(bt show) || die "没有可用的蓝牙适配器，或者蓝牙服务没有运行。$USB_HINT"
    if ! grep -q "Powered: yes" <<<"$ADAPTER"; then
        bt power on >/dev/null || die "无法开启蓝牙适配器。"
    fi
}

list_known_controllers() {
    local devices
    devices=$(bt devices) || die "bluetoothctl devices 失败。"
    KNOWN=$(awk '/DualSense/ {print $2}' <<<"$devices")
}

forget_controllers() {
    local mac
    list_known_controllers
    for mac in $KNOWN; do
        echo "删除配对记录 $mac"
        bt remove "$mac" >/dev/null || die "bluetoothctl remove $mac 失败。"
    done
}

# Succeeds once the scan has found a DualSense, and stores its address in MAC.
controller_discovered() {
    list_known_controllers
    MAC=${KNOWN%%$'\n'*}
    [ -n "$MAC" ]
}

# Pair a controller that BlueZ has no record of. The user has to put it into pairing mode.
pair_controller() {
    local output

    step "蓝牙配对"
    cat <<EOF
请让手柄进入配对模式：
  1. 先确认手柄是关着的（灯条不亮）。亮着的话，长按 PS 键约 10 秒关掉。
  2. 同时按住 Create 键（触摸板左上方的小键）和 PS 键（两个摇杆中间）约 3-5 秒，
     直到灯条开始快速闪烁蓝光再松手。
正在扫描，最多等 $SCAN_SECONDS 秒 ...
EOF

    # BlueZ stores a pairing only while the adapter is bondable, which it is not unless a pairing
    # agent is registered. A pairing that is not stored looks successful, but BlueZ then refuses
    # the controller's input connection ("Rejected connection from !bonded device").
    if ! grep -q "Pairable: yes" <<<"$ADAPTER"; then
        bt pairable on >/dev/null || die "无法把蓝牙适配器设为可配对。"
        RESTORE_PAIRABLE=1
    fi

    bluetoothctl --timeout "$SCAN_SECONDS" scan on >/dev/null 2>&1 </dev/null &
    SCAN_PID=$!
    wait_until "$SCAN_SECONDS" controller_discovered || die "$SCAN_SECONDS 秒内没有发现处于配对模式的手柄。"

    echo "发现手柄 $MAC，正在配对 ..."
    # Trusted, so that the controller may reconnect on its own later, with just its PS button.
    bt trust "$MAC" >/dev/null || die "bluetoothctl trust $MAC 失败。"
    output=$(bt pair "$MAC" 2>&1) || die "配对失败: $(tail -n 1 <<<"$output")"
    kill "$SCAN_PID" 2>/dev/null || true
    SCAN_PID=""

    # The controller may open the input connection itself right after pairing, and this call can then
    # report an error although all is well. Whether the controller works is decided by controller_ready.
    output=$(bt connect "$MAC" 2>&1) || true
    wait_until "$READY_SECONDS" controller_ready ||
        die "配对成功，但输入设备没有出现。bluetoothctl connect 的输出: $(tail -n 1 <<<"$output")"
}

# Connect a controller that is not connected yet: reconnect it if it is paired, pair it if not.
connect_controller() {
    local mac info paired=""

    list_known_controllers
    for mac in $KNOWN; do
        info=$(bt info "$mac") || die "bluetoothctl info $mac 失败。"
        if grep -q "Paired: yes" <<<"$info"; then
            paired="$paired $mac"
        else
            # A record left by an earlier scan would hide whether the controller is in pairing mode now.
            bt remove "$mac" >/dev/null || die "bluetoothctl remove $mac 失败。"
        fi
    done

    if [ -z "$paired" ]; then
        pair_controller
        return
    fi
    step "这台电脑已经配对过手柄:$paired"
    echo "请按一下手柄的 PS 键（两个摇杆中间），最多等 $RECONNECT_SECONDS 秒 ..."
    wait_until "$RECONNECT_SECONDS" controller_ready ||
        die "手柄没有回连。如果它之后配对过别的设备，请运行 ./setup.sh --repair 重新配对。"
}

case "${1:-}" in
    "")       REPAIR=0 ;;
    --repair) REPAIR=1 ;;
    *)        die "用法: ./setup.sh [--repair]" ;;
esac
[ "$#" -le 1 ] || die "用法: ./setup.sh [--repair]"

step "检查环境"
[ "$(uname -s)" = Linux ] || die "只支持 Linux。"
[ -x "$PYTHON" ] || die "找不到 $PYTHON。"
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 8))' || die "需要 Python 3.8 或更高版本，当前是 $("$PYTHON" -V 2>&1)。"
"$PYTHON" -c 'import cairo, gi; gi.require_version("Gtk", "3.0"); gi.require_foreign("cairo"); from gi.repository import Gtk' 2>/dev/null ||
    die "$PYTHON 缺少 GTK 3 图形库。请先安装: sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0"
has_module hid-playstation || die "内核 $(uname -r) 没有 hid_playstation 驱动。它从 Linux 5.12 开始提供，在 Ubuntu 上属于 linux-modules-extra 软件包。Ubuntu 20.04 自带的 5.4 内核没有它，需要换用 HWE 内核: sudo apt install --install-recommends linux-generic-hwe-20.04，然后重启。"
has_module joydev || die "内核 $(uname -r) 没有 joydev 模块（/dev/input/js* 接口）。"
echo "内核驱动和 Python 正常。"

if [ "$REPAIR" = 1 ]; then
    step "删除旧的配对"
    require_bluetooth
    forget_controllers
    pair_controller
elif controller_ready; then
    step "手柄已经连接"
else
    require_bluetooth
    connect_controller
fi

step "自检"
"$PYTHON" dualsense.py
printf '\n完成。\n  开始游戏:    %s/snake.py\n  只显示手柄:  %s/padview.py\n  录制操作:    %s/record.py\n' "$PWD" "$PWD" "$PWD"
echo "以后只要按一下手柄的 PS 键就会自动连上，不需要再运行本脚本。"
