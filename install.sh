#!/bin/sh
set -e
sudo mkdir -p /usr/share/APPLaunch/apps/zerowave /usr/share/APPLaunch/bin /usr/share/APPLaunch/applications /usr/share/APPLaunch/share/images
sudo cp zerowave.py rtlsdr_sweep.py dsd_runner.py aux_decoders.py radio_db.py /usr/share/APPLaunch/apps/zerowave/
sudo chmod 755 /usr/share/APPLaunch/apps/zerowave/zerowave.py
sudo cp zerowave /usr/share/APPLaunch/bin/zerowave
sudo chmod 755 /usr/share/APPLaunch/bin/zerowave
sudo cp zerowave.desktop /usr/share/APPLaunch/applications/zerowave.desktop
sudo cp zerowave.png /usr/share/APPLaunch/share/images/zerowave.png
# Remove legacy ZeroDMR launcher entry if upgrading
sudo rm -f /usr/share/APPLaunch/applications/zerodmr.desktop /usr/share/APPLaunch/bin/zerodmr
echo "ZeroWave installed to /usr/share/APPLaunch!"
