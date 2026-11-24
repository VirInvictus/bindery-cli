# Contributing to bindery-cli

bindery-cli is a small tool with a strict contract, so this page is short and
most of it says where the rules live.

## Set up

```sh
git clone https://github.com/VirInvictus/bindery-cli.git
cd bindery-cli
uv sync --extra reserialize   # a plain `uv sync` drops the html5lib extra
./run_tests.sh                # ruff (CI's pin) and the unittest suite
```

The suite builds its own synthetic EPUBs and needs no Calibre library. The
epubcheck gate is tested against a mocked oracle, so `epubcheck` is optional
for the suite; a few tests also run against the real one when it is installed.

## The rules

- **[spec.md](spec.md) is the contract.** Read it before changing what the
  tool does, and change it in the same commit as the behavior.
- **[AGENTS.md](AGENTS.md) holds the working rules**, the module map, and the
  recorded decisions. It is written for people and for coding agents.
- **Every repair is opt-in and epubcheck-gated.** The default pass is exactly
  the five well-formedness fixes, the NCX pipeline, and the mimetype fix. A
  new fix gets its own flag and never joins that set. If a repair cannot be
  made deterministic and safe, report it instead of repairing it.
- **No new dependencies without discussion.** Open an issue first. The runtime
  set is `tqdm`, `vir-tui`, and `cquarry`, with `html5lib` as the one optional
  extra.
- **The five modules the Calibre plugin vendors must parse on Python 3.11.**
  Keep `except (A, B):` parenthesized. CI byte-compiles the plugin under 3.11
  to 3.14.

## Adding a flag

The "Adding a flag" section of AGENTS.md has the five-step checklist. In short:
add the transform and its `RepairFlags` field, add the row to
`_REPAIR_FLAG_TABLE`, run `scripts/render_repairs_doc.py` to regenerate
`docs/repairs.md` (a test fails when the page and the table disagree), add the
`_flags_from_args` line, and add the spec entry and a test.

## Pull requests

- Tests for the behavior you changed or added.
- CI green: the suite on 3.14, the stack-free modules on 3.12 and 3.13, and
  the plugin parse-check.
- A commit message that says what changed and why.
- No em-dashes in prose the reader sees rendered: README, spec, roadmap,
  patchnotes, commit messages, PR text.

Releases are cut by the maintainer: one tagged release per patchnotes entry.

## Reporting a bug

Open an issue with the command you ran, the output, and the epubcheck result.
Please do not attach a commercial EPUB. The epubcheck code and a few lines of
the markup, or a small synthetic file, reproduce most shapes.

## License

MIT. Contributions are understood to be offered under the same license.
