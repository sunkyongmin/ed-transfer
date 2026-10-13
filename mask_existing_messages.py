import csv, glob, re, shutil
from pathlib import Path

MOBILE_RE = re.compile(r"01[016789][-. ]?\d{3,4}[-. ]?\d{4}")
snap = Path(__file__).parent / "snapshots"
bak = snap / "_premask_backup"
bak.mkdir(exist_ok=True)
total = 0
for f in sorted(glob.glob(str(snap / "messages_*.csv"))):
    f = Path(f)
    shutil.copy2(f, bak / f.name)
    with f.open(encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh); cols = rd.fieldnames; rows = list(rd)
    n = 0
    for r in rows:
        for k, v in r.items():
            if v and MOBILE_RE.search(v):
                r[k] = MOBILE_RE.sub("[휴대전화]", v); n += 1
    tmp = f.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)
    tmp.replace(f)
    print(f"{f.name}: {len(rows)}행 중 {n}칸 가림")
    total += n
print("합계", total, "칸. 원본 백업:", bak)
