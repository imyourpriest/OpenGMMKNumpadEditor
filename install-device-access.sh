#!/bin/sh
# Run this in a normal Ubuntu terminal. Never run the editor as root.
set -eu
cd -- "$(dirname -- "$0")"
printf '%s\n' 'OpenGMMK Numpad Editor — one-time USB access setup' 'This installs a rule for USB 320f:5088 revision 0088, configuration interface 02 only.' 'Ubuntu may ask for your login password. The password is entered into sudo, never the editor.'
sudo /usr/bin/install -o root -g root -m 0644 ./69-gmmk-numpad-configurator.rules /etc/udev/rules.d/69-gmmk-numpad-configurator.rules
sudo /usr/bin/udevadm control --reload-rules
for gmmk_sysfs in /sys/class/hidraw/hidraw*; do
    if /usr/bin/udevadm info --query=property --path="$gmmk_sysfs" | /usr/bin/grep -qx 'ID_VENDOR_ID=320f'; then
        if /usr/bin/udevadm info --query=property --path="$gmmk_sysfs" | /usr/bin/grep -qx 'ID_MODEL_ID=5088'; then
            sudo /usr/bin/udevadm trigger --action=change "$gmmk_sysfs"
        fi
    fi
done
printf '%s\n' 'USB access setup finished. If access is still unavailable, reconnect the USB cable.'
if [ -t 0 ]; then
    printf '%s' 'Press Enter to close this window. '
    read -r gmmk_answer
fi
