#!/bin/zsh
export PATH=/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin:$PATH
cd ~/bigdata/ed-transfer || exit 1
STAMP=$(date +%Y%m%d_%H%M)
DEST=~/Library/Mobile\ Documents/com~apple~CloudDocs/ed-transfer-snapshots
mkdir -p "$DEST"
for f in snapshots/*.csv; do
  gzip -c "$f" > "$DEST/$(basename "$f").gz"
done
cp snapshots/collector.log "$DEST/"
echo "$STAMP backup done (iCloud gz)" >> snapshots/backup.log
