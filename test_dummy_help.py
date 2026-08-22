import argparse

def _add_repair_flags(p):
    p.add_argument("--strip-broken-tags", action="store_true", help="LOSSY: remove leaked HTML closing tags missing their open bracket (e.g. </p> rendered as text) (epubcheck-gated)")
    p.add_argument("--strip-watermarks", action="store_true", help="LOSSY: remove producer/distributor watermarks (e.g. OceanofPDF) (epubcheck-gated)")

dummy = argparse.ArgumentParser(add_help=False)
group = dummy.add_argument_group("available fixes (use with repair or library)")
_add_repair_flags(group)
help_str = dummy.format_help()
print(help_str)
