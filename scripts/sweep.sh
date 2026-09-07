#!/bin/bash
javac --release 25 -cp "/home/bdkl/.local/opt/epubcheck-5.3.0/epubcheck.jar:/home/bdkl/.local/opt/epubcheck-5.3.0/lib/*" FastSweepExtract.java
find "$HOME/docs/Calibre Library" -type f -name "*.epub" > all_epubs.txt
java -cp ".:/home/bdkl/.local/opt/epubcheck-5.3.0/epubcheck.jar:/home/bdkl/.local/opt/epubcheck-5.3.0/lib/*" FastSweepExtract < all_epubs.txt > raw_errors.txt
python3 -c '
import sys
from collections import Counter, defaultdict

error_counts = Counter()
file_errors = defaultdict(set)

with open("raw_errors.txt") as f:
    for line in f:
        line = line.strip()
        if " ||| " not in line: continue
        path, codes_str = line.split(" ||| ", 1)
        if not codes_str: continue
        codes = codes_str.split(",")
        for c in codes:
            error_counts[c] += 1
            file_errors[c].add(path)

print("TOP ERRORS:")
top = error_counts.most_common(500)
for c, count in top:
    print(f"{c}: {count}")

with open("top_errors.txt", "w") as out:
    for c, count in top:
        out.write(f"{c}: {count}\n")
        # Save a few candidate files for each error
        for f in list(file_errors[c])[:5]:
            out.write(f"  {f}\n")
' > sweep_summary.txt
