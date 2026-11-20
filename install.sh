#!/bin/sh
set -e
sudo mkdir -p /usr/share/APPLaunch/apps/zerodmr /usr/share/APPLaunch/bin /usr/share/APPLaunch/applications /usr/share/APPLaunch/share/images
sudo cp zerodmr.py rtlsdr_sweep.py dsd_runner.py /usr/share/APPLaunch/apps/zerodmr/
sudo chmod 755 /usr/share/APPLaunch/apps/zerodmr/zerodmr.py
sudo cp zerodmr /usr/share/APPLaunch/bin/zerodmr
sudo chmod 755 /usr/share/APPLaunch/bin/zerodmr
sudo cp zerodmr.desktop /usr/share/APPLaunch/applications/zerodmr.desktop
sudo cp zerodmr.png /usr/share/APPLaunch/share/images/zerodmr.png
echo "ZeroDMR installed to /usr/share/APPLaunch!"
