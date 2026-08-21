#!/usr/bin/env python3
"""Stand-in for tcpdump, used only by tests/test_pcap.py. Writes growing
chunks to the file passed via -w until killed (SIGTERM), so
backend/capture/pcap.py's rotation/pruning logic can be exercised with a
real process spawn/kill cycle, without needing real tcpdump or actual
network traffic. Every other tcpdump-style argument is ignored.
"""
import sys
import time

args = sys.argv[1:]
path = args[args.index("-w") + 1]

with open(path, "wb") as f:
    while True:
        f.write(b"x" * 4096)
        f.flush()
        time.sleep(0.02)
