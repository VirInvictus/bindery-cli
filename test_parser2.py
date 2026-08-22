import argparse

def _add_repair_flags(p):
    p.add_argument("--strip-broken-tags", action="store_true", help="remove leaked HTML closing tags")

ap = argparse.ArgumentParser(prog="bindery")
# Add to root for --help display
fix_group = ap.add_argument_group("repair flags (can be used with any command)")
_add_repair_flags(fix_group)

sub = ap.add_subparsers(dest="cmd", required=True)
r = sub.add_parser("repair")
_add_repair_flags(r)

args = ap.parse_args(["repair", "--strip-broken-tags"])
print(args)
args2 = ap.parse_args(["--strip-broken-tags", "repair"])
print(args2)
