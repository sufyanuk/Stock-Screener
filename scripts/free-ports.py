"""Stop every process listening on the given TCP ports (default 8000 and 3000), found via /proc.

Used by codespace-start.sh: an older copy of the app (started another way, or a reloader's child process
whose command line does not say "uvicorn") can keep a port, so the new copy cannot start."""
import os
import signal
import sys
import time

ports = {int(p) for p in sys.argv[1:]} or {8000, 3000}


def listening_inodes() -> set[str]:
    inodes = set()
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            rows = open(f).read().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            cols = row.split()
            port = int(cols[1].rsplit(":", 1)[1], 16)
            if cols[3] == "0A" and port in ports:  # 0A = LISTEN
                inodes.add(cols[9])
    return inodes


def holders() -> set[int]:
    inodes, pids = listening_inodes(), set()
    if not inodes:
        return pids
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                link = os.readlink(f"/proc/{pid}/fd/{fd}")
                if link.startswith("socket:[") and link[8:-1] in inodes:
                    pids.add(int(pid))
                    break
        except OSError:
            continue
    pids.discard(os.getpid())
    return pids


for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 3)):
    pids = holders()
    if not pids:
        break
    print(f"stopping old app process(es) {sorted(pids)} on port(s) {sorted(ports)}")
    for p in pids:
        try:
            os.kill(p, sig)
        except OSError:
            pass
    for _ in range(wait * 2):
        if not holders():
            break
        time.sleep(0.5)
left = holders()
if left:
    print(f"! port(s) still in use by {sorted(left)}")
    sys.exit(1)
