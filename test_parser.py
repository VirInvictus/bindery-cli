import argparse

def _add_repair_flags(p):
    p.add_argument("--strip-broken-tags", action="store_true", help="remove leaked HTML closing tags")
    p.add_argument("--strip-watermarks", action="store_true", help="remove watermarks")
    p.add_argument("--all", action="store_true", help="enable all individual fix flags")

shared = argparse.ArgumentParser(add_help=False)
_add_repair_flags(shared)

ap = argparse.ArgumentParser(prog="bindery", description="Repair EPUBs, epubcheck-gated.")
# Add an explicit group to root so it shows up in --help nicely without being parsed twice if we just want it for display?
# Wait, if we use parents=[shared], let's see how --help looks.
ap_with_parents = argparse.ArgumentParser(prog="bindery", description="Repair EPUBs, epubcheck-gated.", parents=[shared])
sub = ap_with_parents.add_subparsers(dest="cmd", required=True)

r = sub.add_parser("repair", help="repair a single EPUB to a new file")
# We don't need to add_repair_flags(r) if it's on the root, but then users MUST put flags before the subcommand.
# "bindery repair --strip-broken-tags" -> error: unrecognized arguments.
# If we want it to work AFTER the subcommand, we must put it on the subparser.
