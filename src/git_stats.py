import subprocess
from collections import defaultdict

out = subprocess.run(
    [
        "git",
        "log",
        "--since=2026-05-22 00:00",
        "--pretty=format:%ad",
        "--date=format:%Y-%m-%d",
        "--shortstat",
    ],
    capture_output=True,
    text=True,
).stdout

stats = defaultdict(lambda: {"commits": 0, "files": 0, "ins": 0, "del": 0})
cur = None
for line in out.splitlines():
    s = line.strip()
    if not s:
        continue
    if s.startswith("2026-"):
        cur = s
        stats[cur]["commits"] += 1
    elif "changed" in s and cur:
        for p in s.split(","):
            p = p.strip()
            if "file" in p:
                stats[cur]["files"] += int(p.split()[0])
            elif "insertion" in p:
                stats[cur]["ins"] += int(p.split()[0])
            elif "deletion" in p:
                stats[cur]["del"] += int(p.split()[0])

header = f"{'день':12s}  {'коммитов':>9s}  {'файлов':>7s}  {'+строк':>8s}  {'-строк':>8s}  {'нетто':>9s}"
print(header)
print("-" * len(header))

tot_c = tot_f = tot_i = tot_d = 0
for day in sorted(stats):
    v = stats[day]
    net = v["ins"] - v["del"]
    print(f"{day:12s}  {v['commits']:>9d}  {v['files']:>7d}  {v['ins']:>+8d}  {-v['del']:>+8d}  {net:>+9d}")
    tot_c += v["commits"]
    tot_f += v["files"]
    tot_i += v["ins"]
    tot_d += v["del"]

print("-" * len(header))
print(f"{'ИТОГО':12s}  {tot_c:>9d}  {tot_f:>7d}  {tot_i:>+8d}  {-tot_d:>+8d}  {tot_i - tot_d:>+9d}")
print()
print(f"среднее в день:  ~{tot_c / len(stats):.1f} коммитов, ~{tot_i / len(stats):.0f} строк добавлено")