#!/bin/zsh
export PATH=/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin:$PATH
cd ~/bigdata/ed-transfer || exit 1
STAMP=$(date +%Y%m%d_%H%M)
DEST=~/Library/Mobile\ Documents/com~apple~CloudDocs/ed-transfer-snapshots
mkdir -p "$DEST" snapshots_server
if rsync -az -e "ssh -o BatchMode=yes -o ConnectTimeout=20" edc:/opt/ed-collector/snapshots/ snapshots_server/; then
  echo "$STAMP server pull ok" >> snapshots/backup.log
else
  echo "$STAMP server pull FAILED" >> snapshots/backup.log
fi
for f in snapshots/*.csv(N); do gzip -c "$f" > "$DEST/$(basename "$f").gz"; done
for f in snapshots_server/*.csv(N); do gzip -c "$f" > "$DEST/server_$(basename "$f").gz"; done
cp snapshots/collector.log "$DEST/" 2>/dev/null
cp snapshots_server/collector.log "$DEST/server_collector.log" 2>/dev/null
echo "$STAMP backup done (iCloud gz)" >> snapshots/backup.log
