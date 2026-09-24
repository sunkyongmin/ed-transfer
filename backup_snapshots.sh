#!/bin/zsh
cd ~/bigdata/ed-transfer || exit 1
STAMP=$(date +%Y%m%d_%H%M)
mkdir -p ~/Library/Mobile\ Documents/com~apple~CloudDocs/ed-transfer-snapshots
cp snapshots/*.csv snapshots/collector.log ~/Library/Mobile\ Documents/com~apple~CloudDocs/ed-transfer-snapshots/
git add snapshots
git commit -m "snapshots backup $STAMP" >> snapshots/backup.log 2>&1
git push >> snapshots/backup.log 2>&1
echo "$STAMP backup done" >> snapshots/backup.log
