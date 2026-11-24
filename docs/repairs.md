# Opt-in repairs reference

<!-- Generated from _REPAIR_FLAG_TABLE in src/bindery/cli.py by
     scripts/render_repairs_doc.py and pinned by tests/test_repairs_doc.py.
     Edit the table, not this page. -->

Everything on this page is off until its flag is passed, or all at once via
`--all`. It is the same reference `bindery repair --help-repairs` prints,
generated from the CLI's own flag table so the two cannot drift. `bindery
--help-json` dumps the whole surface machine-readable, and
[spec.md](../spec.md) is the contract this page describes.

The always-on core pass is exactly five well-formedness fixes (strip prolog
junk, drop duplicate xmlns, escape bare ampersands, fix named entities,
self-close void tags), the NCX pipeline, and the mimetype fix. Everything below
is opt-in, and every repair is epubcheck-gated: a candidate is applied only
when epubcheck accepts it under one of three acceptance bars. The default gate
demands a measured improvement. The no-worse bar, for the lossy strips,
`--fix-cover`, `--encode-url-spaces`, and `--fix-media-types`, only forbids a
regression, because their gains sit where epubcheck's counts do not look.
`--encode-ref-delims` rides a bar keyed on its fix having fired. `--all`
enables every flag below at once, and library mode is dry-run by default.

Statuses a record can carry: accept (gate-applied), partial (improved but still
failing epubcheck; written only by the single-file form with an output path,
never applied to a library), reject (the gate refused), error, unreadable,
nochange (nothing to do), equal (already as good as the result), unvalidated
(applied under `--no-validate`, never gate-checked).

## How repairs are accepted

Three acceptance bars apply. The safety contract in the README explains why
each exists, and spec.md is the contract.

- **`gate` (the default)**: the candidate is applied only when it measurably
improves the book: strictly fewer fatals on a book that had fatals, or strictly
fewer errors on one that did not. A run with no measurable gain applies nothing
to a library and writes nothing on a probe. The single-file form with an output
path still writes such a candidate (a known limitation).

- **`no_worse`**: `--fix-cover`, `--encode-url-spaces`, `--fix-media-types`,
and the four lossy strips are accepted when the result is not worse (no net-new
fatals, and no new errors unless fatals were already masking them), because
their gains sit on axes epubcheck's counts do not measure: cover wiring is not
a schema finding, the rename half's PKG-010 gain sits on the warning axis, the
font half's CSS-007 gain on the INFO axis, and a lossy strip's gain is
invisible to epubcheck.

- **the delimiter bar**: `--encode-ref-delims` is accepted by a bar keyed on
its fix having fired (the missing-file mask): net-new fatals reject, a
still-fatal candidate stays partial, and a fatal-free candidate accepts
whatever the unmasking did to the error count.

The bar is chosen per candidate, keyed on whether one of these fixes fired in
it: in a candidate where a no-worse fix fired, every fix in that candidate is
judged by the no-worse bar. One structural veto rides above all three: a
candidate whose spine resolves worse than the source's (more dangling itemrefs,
or fewer than half the documents resolved after a dead-spine prune) is rejected
whatever the counts say.

## Safe opt-ins

*Markup-neutral.*

### `--fix-ids`

rewrite invalid OPF/NCX ids (RSC-005), every reference in sync.

Rewrite ids that are not valid XML names in the OPF manifest and in the NCX. An
id qualifies when it starts with a digit, contains a colon, or carries any
character outside the XML NameChar set, such as the apostrophes calibre copies
from filenames. Old conversions stamp NCX navPoint ids from UUIDs, which is the
common NCX shape.

Every reference follows the rename: the spine's `idref` and `toc`, an item's
`fallback` and `media-overlay`, and the EPUB 2 cover meta, so a table of
contents never dangles against the ids it names. The reference matchers carry
the same `data-*` guard the id matchers do, so a `data-fallback`, `data-toc`,
or `data-content` value is never rewritten.

`dc:` metadata is never altered. The flag touches the OPF, which is why it is
opt-in.

### `--add-img-alt`

add alt="" to &lt;img&gt; elements missing the required attribute.

Add `alt=""` to `<img>` elements that are missing the required attribute. It
renders identically, but it adds markup the author never wrote, and an empty
`alt` asserts "decorative" to a screen reader where a missing one did not.
Hence opt-in.

### `--reserialize`

rebuild still-malformed documents via html5lib.

Rebuild content documents that still fail to parse by re-parsing them with
html5lib and re-emitting XHTML. It closes unclosed non-void elements (`<p>`,
`<div>`, `<span>`, `<blockquote>`, and the like) that the regex transforms
cannot reach.

It runs only on documents that raise an XML parse error, so documents that
already parse are left alone even when they carry content-model errors
(wrapping those would change layout). It needs an `<html>` root to anchor on
and refuses non-HTML XML sidecars. This is the one repair that depends on
html5lib, the optional `reserialize` extra.

### `--strip-bad-attrs`

drop invalid attributes (digit-led names, unbound prefixes).

Drop attributes that make the XML unparseable: a name starting with a digit (a
mangled `31=""`), or a namespaced name whose prefix is not declared anywhere in
the document (Office VML's `v:shapes`). Surgical, and a no-op on well-formed
files.

### `--escape-unknown-entities`

&amp;foo; -&gt; &amp;amp;foo; for names outside the HTML5 table.

Escape entity names that are not in the HTML5 table (`&foo;` becomes
`&amp;foo;`), which renders exactly as browsers already render an unknown
entity. Documents whose DOCTYPE carries an internal subset are skipped
wholesale, since a subset can declare custom entities.

### `--fix-double-colon-tags`

escape unescaped pseudo-tags whose name carries :: (a fatal QName break).

Escape the `<` and `>` around pseudo-tags whose name contains a double colon,
such as a C++ sample like `std::vector<std::shared_ptr>` whose converter
escaped the trailing bracket but not the tag. An XML name carries at most one
colon, so such a construct is never markup, and the parser dies on it with a
fatal RSC-016.

When the pseudo-tag has a raw closing `>`, escaping both ends restores exactly
the text the author wrote, and real markup is unreachable because the double
colon lives in the name grammar itself. A nested template is escaped over
successive passes until the result is stable (at most 16 passes).

Known limitation: the text after the name runs to the first raw `>` up to 120
characters away. When the pseudo-tag's own closing bracket is entity-escaped
(`&gt;`), everything up to that next `>` is escaped with it, real markup
included.

## Structural repairs: markup and nesting

### `--strip-presentational-attrs`

strip deprecated presentational attributes (align/valign/bgcolor/border, body link/vlink, list type).

Strip the deprecated presentational hints the XHTML 1.1 DTD rejects, wherever
they appear: `align`, `valign`, `border`, `bgcolor`, `vspace`, `hspace`, and
`compact`; the body element's `link`, `vlink`, `alink`, `text`, and
`background` family; `type` on list elements; and Amazon's `data-AmznRemoved`
scraper junk. `type` is legal and load-bearing on `style`, `link`, and
`script`, which are never touched.

The edit is anchored to real start tags and never touches CDATA or comments.

Known caveat: these are presentational hints, and readers that honor them
render differently after the strip (`bgcolor` stops coloring, `align` stops
centering).

### `--fix-empty-body`

fill a strictly empty &lt;body&gt;&lt;/body&gt; with &lt;p&gt;&amp;#160;&lt;/p&gt;.

Fill a strictly empty `<body></body>` with one minimal paragraph,
`<p>&#160;</p>`, to clear the "body incomplete" error. The numeric character
reference needs no DTD (a named `&nbsp;` is an undeclared-entity parse fatal
under every DOCTYPE epubcheck does not resolve locally), and the wrapping `p`
is the block element XHTML 1.1's body content model demands, which bare text
never satisfied.

It adds visible content the author never wrote, which is why it is opt-in.

### `--fix-missing-title`

inject &lt;title&gt;Unknown&lt;/title&gt; when the head has none.

Inject a `<title>Unknown</title>` fallback when the head has no usable title:
an empty `<title/>` is filled in, and an absent tag is added to the head.

The fill is scoped to the `<head>` when the document has one, so an SVG
`<title>` (visible text: the image's accessibility name) is never filled there.
Known limitation: a document with no `<head>` is filled document-wide, and an
empty SVG `<title>` in such a document receives the fallback too.

### `--fix-id-colons`

id="X:Y" colons and matching #X:Y fragments become underscores.

Rename any content-document `id` that cannot be an XML NCName to a valid one,
and translate internal fragment references with it. That covers a colon in
`id="X:Y"` with its matching `#X:Y` fragments (both become underscores),
digit-led scraped ids, and apostrophe-bearing calibre ids (the same
invalid-name check as the manifest half, `--fix-ids`; the content half adds the
`id_` prefix only when the name cannot start with its own first character). The
NCX's `content src` fragments follow the same predicate, so the table of
contents never dangles against the ids it references. The fragment matchers
translate an apostrophe-bearing value such as `href="c1.xhtml#Lisey's_story"`
whole.

Three things are never touched: the fragment of an external URL (it names a
position in another document), `data-id` values, and an escaped fragment (a
percent-escape like `#caf%C3%A9` or an entity reference). An escaped fragment
names its target under decoding, and renaming the encoded form would
manufacture a target that exists nowhere.

### `--unwrap-block-in-inline`

unwrap a &lt;span&gt; illegally wrapping a block element.

Unwrap a `<span>` that illegally wraps a block element (`<div>`, `<p>`,
`<blockquote>`), keeping the block element and its text.

### `--strip-invalid-value`

strip misplaced value= attributes from non-form elements.

Strip misplaced `value="..."` attributes from elements like `<div>`, `<span>`,
and `<p>`, where they are schema violations. A `data-value` attribute is
arbitrary data and is never touched.

### `--unwrap-illegal-tags`

delete illegal tags (&lt;st&gt;, &lt;w&gt;...) keeping text; styled names safe.

Delete illegal or deprecated HTML tags that break EPUB 3 validation (`<st>`,
`<sentence>`, `<o>`, `<w>`, `<pagebreak>`) while keeping their inner text.

A name that an EPUB stylesheet styles as an element selector is protected for
the whole book, so styled formatting survives. Both `.css` entries and inline
`<style>` blocks count, and only element selectors do: a class or id selector
like `.st` or `#w` does not protect the tag.

### `--strip-epub3-attrs`

scrub EPUB3-only attributes an EPUB2 package rejects (fixed set).

Scrub the EPUB 3-only attributes epubcheck rejects on an EPUB 2 package:
`page-progression-direction`, `epub:type`, `aria-label`, `role`, and
`aria-hidden`. It is a fixed, documented set, and rendering is unchanged.
Lookalikes such as `type` and `aria-labelledby` survive, and so does `hidden`,
since stripping it would reveal hidden content.

The edit is anchored to real start tags, so prose that merely mentions
`epub:type="chapter"` is preserved. The flag is gated on the package version
declared in the OPF: on an EPUB 3 book, or when no version can be read, it does
nothing, because its target defects only exist below EPUB 3.

### `--downgrade-epub3-tags`

figure/section/nav/header to div, figcaption to p; semantic name kept as class.

Downgrade EPUB 3 and HTML5 semantic elements to their EPUB 2 equivalents:
`figure`, `section`, `nav`, and `header` become `div`, and `figcaption` becomes
`p`. Existing classes are kept and the semantic name is appended as the styling
hook (`class="figure"`).

A tag name that a stylesheet styles as an element selector is protected
book-wide, so styled formatting is never destroyed. Comments and CDATA are
never rewritten, and a `data-class` attribute is never mistaken for the class
hook.

Like `--strip-epub3-attrs`, the flag is gated on the package version: it does
nothing on an EPUB 3 book or when no version can be read, and the version read
accepts a namespace-prefixed package element (`<opf:package version="2.0">` is
an EPUB 2 declaration).

### `--fix-misnested-inline`

rewrite the drop-cap mis-nest &lt;i&gt;&lt;b&gt;X&lt;/i&gt;&lt;/b&gt; to &lt;i&gt;&lt;b&gt;X&lt;/b&gt;&lt;/i&gt;.

Rewrite the Mobipocket drop-cap mis-nest `<i><b>X</i></b>` to
`<i><b>X</b></i>`, the one well-formed form of the same two spans
(`em`/`strong` variants and either tag order are covered).

Only the both-open-then-reversed-close shape is touched, and only within
bounds: the start tags must be bare (no attributes), the shared text between
them 1 to 64 characters, and at most 8 punctuation characters may sit between
the reversed closers. A punctuation run caught there (`<i><b>xile</i>,</b>`)
survives outside both spans, and a letter run is refused, because which span
keeps the letters has no deterministic answer.

### `--fix-stray-close`

remove end tags whose element has no open start (stray &lt;/div&gt;).

Remove an end tag whose element has no open start tag anywhere above it: the
stray extra `</div>` on a shell page, which cascades into "body must be
terminated by the matching end-tag".

A merely mis-nested pair (`<div><span></div></span>`) is never touched. A close
is removed only when nothing by that name is open.

### `--fix-unterminated-attr`

close attribute values open to the tag's own '&gt;' (RSC-016).

Close an attribute value left open to the tag's own `>` (`<p class="footnote>`
becomes `<p class="footnote">`). The parser swallows prose into the value until
the next quote and dies on the first `<` it meets.

Guards keep it off legal `>`-bearing values: only double-quoted values are
judged, the value must be one bare word, and no quote may close before the next
`<`.

### `--fix-cdata-terminator`

complete truncated /*]]&gt; closers in inline &lt;style&gt; blocks (CSS-008).

Complete a truncated CDATA-terminator comment in an inline `<style>` block:
`/*]]>` becomes `/*]]>*/`. A comment-wrapped CDATA block whose closing `*/` the
converter dropped leaves the CSS parser inside an unterminated comment, one
CSS-008 "Premature end of file" per affected document.

Only the malformed-terminator token class is rewritten. Invalid CSS stays
unfixed, and stylesheet files are not touched.

## Structural repairs: references and resources

### `--strip-dead-font-faces`

drop @font-face blocks whose url() references all miss the archive (RSC-007).

Drop every `@font-face` block in a stylesheet whose `url()` references all miss
the archive. The shape is ebook-convert's azw3 to EPUB output: it carries the
source's font declarations without the font payloads, filling `page_styles.css`
with `url(styles/XXXXXXXXXXXXXXXX)` placeholders. Each dead reference is an
RSC-007 the manifest prune cannot reach, because the OPF never declared the
fonts.

The drop is rendering-neutral: the font never loaded, so the fallback is what
the file already does. One live `url()` keeps the whole block, and so does
anything that cannot be read exactly: a scheme, query, fragment, raw-space, or
`local()`-only reference, or a block with nested braces. Comments are never
rewritten, XML and plain CSS alike. Runs on `.css` entries.

### `--prune-dead-spine`

the shell repair: drop manifest items and spine positions whose files the archive never carried.

The shell repair. It fires only at the shell signature: half or more of the
manifest's content hrefs name files the archive does not carry, the same shape
the audit's `shell` analyzer reports. It then drops the dead manifest items,
the spine itemrefs that name them, the dead NCX navPoints, and the OPF guide
references to the dead files, and re-sequences the surviving playOrder values
to 1..K. The re-sequence is unconditional: epubcheck demands the 1 base on the
post-prune shape.

Two kinds of item look dead and are not counted as dead. When
`--encode-url-spaces` runs alongside, a raw-space href whose file exists in the
source archive is rename material, not a dead item. An item whose file exists
under a delimiter-encoded spelling is refused too: those hrefs are
`--encode-ref-delims`' repair class, and pruning them would amputate live
documents while the counts read it as an error win.

A lone missing chapter never crosses the signature. Below it nothing is
dropped: a present spine document is never pruned, and an ordinary missing
chapter is reported, not removed.

### `--encode-ref-brackets`

percent-encode raw [ ] in internal references whose target exists (RSC-020).

Percent-encode `[` and `]` in the path portion of internal `src`/`href`
references whose archive target exists with the raw brackets (RSC-020, "Illegal
character in path segment"). epubcheck decodes a reference before it looks the
entry up, so the encoded spelling resolves to the raw-spelled entry.
`--encode-url-spaces` performs this encoding only while renaming space-bearing
entries; this flag covers brackets without spaces.

The fragment and query portions are left untouched, entry names are never
renamed, and unresolvable references stay as written. Runs on content
documents, the OPF, the NCX, and stylesheet `url()` tokens.

### `--encode-ref-delims`

percent-encode raw # ? that defeat their own reference's resolution (RSC-001).

Percent-encode a literal `#` or `?` inside a filename reference whose raw
delimiter defeats the reference's own resolution. In `href="Book_#01.htm"` the
reference parses as file `Book_` plus fragment `01.htm`; the manifest item goes
missing (RSC-001, "File ... could not be found"), and epubcheck never parses
the document at all.

Two gates are required. The reference's path portion, read the way epubcheck
parses it (the first `#` or `?` starts the fragment or query), must miss the
archive, so a working `doc.xhtml#intro` is never touched and where both `a` and
`a#b` exist the standard spelling wins. And the delimiter-encoded spelling must
match an archive entry.

Two values are refused outright: one whose attribute text carries a numeric
character reference anywhere (`&#`; the entity's own spelling contains a raw
`#`, and the rewrite would corrupt it), and one whose path portion is empty
(`#intro` is an in-document anchor, never filename damage). Entry names stay
raw-spelled, and a genuinely missing file stays as written. Runs on content
documents, the OPF, the NCX, and stylesheet `url()` tokens, beside
`--encode-ref-brackets`.

Acceptance has its own bar, keyed on this flag having fired: net-new fatals
reject, a candidate that is still fatal stays partial, and a fatal-free
candidate is accepted whatever the unmasking did to the error count. The spine
veto still overrides.

### `--fix-page-map`

drop the OPF page-map attribute; class="pages" on NCX pageLists.

Normalize legacy page-map markup: drop the non-standard `page-map="..."`
attribute from the OPF `<spine>`, and add `class="pages"` to NCX `<pageList>`
elements that have none (epubcheck rejects both on older HarperCollins and
Anna's Archive conversions). A `<pageList>` with no plain `id` also gets
`id="bindery-pagelist"` (suffixed if that id is taken), because epubcheck
demands the id once the class is present. The checks read the plain attributes
only, so a `data-class` or `data-id` never suppresses the insertion epubcheck's
demand needs.

### `--prune-missing-resources`

remove references to files the archive does not contain (RSC-007).

Remove references to files the archive does not contain (RSC-007, PKG-010):
dead `<link>` elements, anchors' `href` to absent files (the anchor text is
preserved), absent `<img>` sources (replaced by their alt text when they carry
one), and orphaned non-spine OPF manifest items.

Every package edge that pointed at a pruned item is rewritten: the spine's
`toc`, `media-overlay`, `fallback`, the EPUB 2 cover meta, and the OPF guide
`<reference>` elements naming the pruned files. A reference left behind would
read as new errors the prune itself manufactured. In a run that also drops stub
documents, a surviving document's link into a dropped stub is treated as absent
and stripped here, with its anchor text kept.

Spine documents are never pruned. A missing spine document is reported as a
damaged fragment, not silently dropped.

### `--strip-broken-anchors`

strip hrefs that cannot resolve; anchor text always preserved.

Strip `href` attributes that cannot resolve, keeping the anchor text byte for
byte. Two classes qualify: a `#fragment` the target document does not define
(RSC-020, RSC-012), and unresolvable URI schemes (`kindle:`, `file:`). NCX
navTargets and OPF guide references keep their document target, so chapter
navigation survives.

Fragments are percent-decoded before the existence check, so a valid link to a
non-ASCII id (`#caf%C3%A9` naming `id="café"`) is kept. When NCX fragments are
stripped, the always-on playOrder pass re-runs on the final navMap, so two
fragments collapsing onto one document cannot leave the playOrder contract
violated.

### `--prune-dangling-ncx-targets`

remove NCX navPoints/pageTargets whose content src is absent (RSC-007).

Remove NCX `navPoint`, `navTarget`, and `pageTarget` elements whose `<content
src>` names a document the archive does not contain (RSC-007 at the toc).
`--prune-missing-resources` cannot reach this class, because the OPF never
declared the dropped file.

The prune is node-exact. A dead node whose whole subtree dies with it drops as
one span. A dead node above surviving descendants is spliced: its tags and its
own label go, and the children re-home one level up, because dropping live
navigation to repair a dead label is no repair. Scheme URLs, bare fragments,
and contentless nodes are never this class. An unbalanced walk aborts
unchanged, and a prune that would empty a container refuses, since an empty
navMap is itself an error.

When it fires, the always-on playOrder pass re-runs on the final navMap, so the
table of contents stays valid in one run.

### `--encode-url-spaces`

encode raw spaces in URLs; rename space-bearing entries (PKG-010).

Repair raw-space URLs two ways. It percent-encodes raw spaces in `src`/`href`
values across the package (OPF manifest, NCX, content documents), because a URL
with a literal space is not a valid URL (RSC-020) and does not resolve on
strict readers. And it renames archive entries whose names carry raw spaces to
their underscore spellings, rewriting every reference to them (OPF, NCX,
content, CSS `url()`), which clears the PKG-010 space-in-filename advisories.

The rename uses underscores, never percent-encoding, because epubcheck decodes
references before entry lookup. `[` and `]` in a renamed reference are
percent-encoded, since epubcheck rejects them raw in a URL path. A rename that
two entries would both claim is refused per entry, and a book with any
non-UTF-8 document keeps all of its entry names (those entries pass through
byte-for-byte, with no chance to rewrite their references). Both halves rewrite
only attributes inside real start tags and processing instructions: a book that
displays HTML in its prose carries `href="..."` in its text, and a bare
attribute regex corrupts the next real tag.

Accepted under the no-worse bar: the rename half's gain sits on the warning
axis.

### `--fix-svg-dup-ids`

rename duplicate glyph ids in .svg entries (first keeps its name).

Rename duplicate `id` values inside standalone `.svg` entries, the shape old
calibre SVG page renders produce when they repeat glyph ids within one
document. The first occurrence keeps its name and later ones gain `_2`, `_3`,
and so on.

Internal references are deliberately not rewritten: they already resolve to the
first definition under every reader's first-match lookup. Duplicate ids in the
content documents themselves are `--fix-duplicate-ids`.

### `--fix-duplicate-ids`

rename duplicate ids in content documents (first keeps its name).

Rename duplicate `id` values within each content document (the RSC-005
"Duplicate id" class on `.xhtml` and `.html` carriers), by the same rule as the
SVG fix: the first occurrence keeps its name, later ones gain `_2`, `_3`, and
so on, and no reference is rewritten. `href="#x"` anchors, CSS `#x` selectors,
and cross-document fragments all resolve to the first definition, exactly as
readers resolved them before.

Commented-out and CDATA-hidden tags do not count as duplicates. They are
invisible to epubcheck and to every reader, and counting them would rename the
real definition away from its name.

## Structural repairs: package wiring

### `--fix-container`

generate container.xml when missing or stale (the gateway defect).

Generate `META-INF/container.xml` pointing at the located OPF when the
container is missing or names a file the archive does not contain. This is the
gateway defect: epubcheck stays fatal while the OPF is unfindable, so no repair
can be gate-accepted on such a book until the container exists.

### `--fix-media-types`

normalize wrong manifest media-types (magic-byte confirmed).

Normalize wrong manifest media-type declarations (OPF-029, for example a JPEG
stamped `image/png` by an aggregator). The font slice is covered too: a `ttf`,
`otf`, `woff`, or `woff2` whose declared type is not the exact `font/*`
spelling (for example `application/x-font-ttf`) is rewritten to it.

The edit is attribute-only and quote-preserving, and it fires only when the
file's magic bytes confirm the extension, so a misnamed-but-consistent file (a
PNG renamed `.jpg`) is never made worse. `.eot` is never touched, since
epubcheck rejects EOT as a font source however it is declared.

Accepted under the no-worse bar: the font half's gain sits on the INFO axis,
which the counts do not measure.

### `--declare-missing-items`

declare referenced-but-undeclared files present in the archive (RSC-008).

Add the manifest `<item>` declarations the OPF never wrote for files the book
references and the archive carries (RSC-008): content `<img src>` and `<link
href>` targets, CSS `url()` resources, and OPF guide references.

Ids are deterministic (`bindery-dN`, numbered in sorted-target order and
`_`-prefixed until free) and media types come from a blessed extension map
only; anything else is skipped rather than guessed. No `<itemref>` is ever
written, so the spine is untouched, and an itemref with no manifest item is
deliberately not repaired: which file it meant is underdetermined. NCX
`<content src>` targets are not scanned. Namespace-prefixed packages declare
too.

### `--fix-unique-identifier`

wire the dangling OPF unique-identifier onto the first dc:identifier (OPF-030).

Wire the dangling OPF unique-identifier (OPF-030, "The unique-identifier X was
not found"). The id the package's `unique-identifier` attribute names is added
to the first `dc:identifier` start tag when no element carries it. Only that id
is written; the `dc:` values are never touched.

The NCX `dtb:uid` is aligned in the same run, since the broken wiring was
masking that comparison.

It refuses to guess. An absent or empty attribute, an id that is not a valid
XML name, an OPF that is already wired, no `dc:identifier` element, or a first
identifier that already carries an id each leave the document untouched.

### `--fix-cover`

re-point or remove a dangling EPUB2 cover meta (no-worse bar).

Repair dangling EPUB 2 cover wiring. A `<meta name="cover">` whose content
names no manifest item is re-pointed when the OPF guide's own cover reference
names exactly one existing item, and removed when nothing identifies it. EPUB
3's `properties="cover-image"` is deliberately audit-only, since guessing which
image is the cover is not deterministic.

Cover wiring is invisible to epubcheck, so cover-only repairs are accepted
under the no-worse bar rather than the normal gate.

### `--fix-comment-double-hyphen`

replace -- inside XML comments with an en-dash (RSC-016).

Replace `--` sequences inside XML comments with en-dashes. `--` is illegal
inside an XML comment (a fatal RSC-016: the book opens in lenient readers but
never passes epubcheck).

The edit stays inside comment bodies in content documents, the NCX, and the OPF
alike. Text nodes and CDATA sections are never touched, and each comment's
terminator is left intact. This is the one deliberate exception to the rule
that comments are never rewritten.

### `--fix-unbound-prefix`

bind an undeclared namespace prefix inline on the element that uses it.

Bind a prefix the OPF never declares in scope, inline on the element that uses
it (a fatal RSC-016, "prefix is not bound"). Two shapes left by calibre's
metadata writer qualify: a `dc:identifier` carrying `ns0:scheme` while its
`xmlns:ns0` declaration sits on a sibling, out of XML scope; and a `dc:`
element whose own name is unbound because every other `dc:` element
self-declares `xmlns:dc` except that one. Either one fatals the whole package.

The namespace URI is evidence-based, never guessed: the same prefix declared
elsewhere in the document, with every declaration agreeing, or the use site's
own namespace under an OPF-namespaced package (the OPF namespace for a Dublin
Core element's attributes, the DC namespace for a `dc:` element name). Anything
else refuses the document untouched.

Runs on the OPF only. Content documents are not touched; their unbound prefixes
have the `--strip-bad-attrs` drop.

### `--fix-package-version`

rewrite package version="1.0" to "2.0" (OPF-001).

Rewrite a `<package>` element whose version attribute is the literal `1.0` to
`2.0` (OPF-001: an EPUB version epubcheck cannot parse). Only the exact literal
qualifies. A missing attribute (an OEB 1.0 package needs a migration, not an
attribute bump) or any other spelling refuses the document untouched, and a
`data-version` attribute is arbitrary data, never rewritten. The gate decides
whether the revalidation under the new profile is a real decrease.

### `--fix-doctype`

replace a forbidden DOCTYPE with the profile's expected form (HTM-004).

Replace each content document's DOCTYPE when it differs from the form its
package profile expects (HTM-004). EPUB 3 expects `<!DOCTYPE html>`, so an
XHTML 1.x public doctype is a violation. EPUB 2's OPS content docs are XHTML 1.1, so an XHTML 1.0 doctype, or a bare `<!DOCTYPE html>`, is replaced with the
XHTML 1.1 declaration. The package version must be known: an unknown version
licenses nothing.

A declaration that differs only in spacing is left alone, and an absent DOCTYPE
is legal in both profiles and is never inserted. A DOCTYPE carrying an internal
subset is never touched, since the subset declares entities and swapping it
would change what the parser resolves.

### `--prune-invalid-guide-refs`

remove guide references whose target is not a content doc (OPF-032).

Remove guide `<reference>` elements whose target is declared in the manifest
but is not an OPS content document (OPF-032; the common fingerprint is a Random
House guide block pointing cover references at JPEGs). Both self-closed and
paired elements are removed. The guide is furniture, so removal is
content-neutral.

A reference to an undeclared file is OPF-031, `--declare-missing-items`' lane,
and is left alone, as is an external scheme.

### `--fix-item-properties`

declare scripted/svg/remote-resources on EPUB3 manifest items (OPF-014).

Scan each content document for what epubcheck wants declared (OPF-014, "The
property X should be declared in the OPF"): `scripted` from a `<script>`
element or a `<form>`, `svg` from an `<svg>` element, and `remote-resources`
from an absolute-scheme `src`, `link href`, or CSS `url()`. The tokens are
merged into the owning manifest item's `properties` attribute, sorted and
deduplicated.

EPUB 3 packages only: on EPUB 2 the attribute is itself a finding, so there the
flag does nothing and the finding stays in the report. Declares reality and
changes no content.

## Lossy strips

*These delete converter-injected content; accepted on a no-worse bar.*

### `--strip-pagination`

LOSSY: page numbers/running heads baked into the body text.

Remove print page numbers and running headers that a PDF or OCR conversion
baked into the body text as literal paragraphs, where they reflow into the
middle of a sentence ("where the hay cart **16** was taking him"). It removes
only that injected furniture, never the author's prose. Where a number split a
sentence it rejoins the two paragraphs, closing up a word split like `compli-`
/ `mentary`, and it preserves roman chapter numbers, page-list nav anchors, and
years.

A roman numeral counts as a page number only when it is well-formed and under
100, so ordinary words (`mid`, `mix`, `civil`) are never mistaken for pages. A
book is treated as paginated only when it has both a dense run of arabic
numbers and several confident mid-sentence interrupts, so a merely
chapter-numbered book is left alone. Navigation anchors are real ids only (`id`
and `xml:id`); a `data-id` is arbitrary data no reader can resolve.

Every removal requires prose flow. A digit enclosed by foreign markup on both
sides (a lone recipe-quantity table cell) is layout content, never page
furniture, and a confident merge runs only when the whole corridor between the
merged paragraphs is plain text. A document whose standalone-number values are
substantially shared with other documents (25% or more) carries the book's own
numbering (verse stanzas, sections that restart per unit, so their values
reappear, unlike page numbers, which are unique), and the strip takes no
numbers from it.

Four safety nets guard every edit: character conservation, `p`/`a` tag balance,
an all-tag open/close delta check, and the epubcheck no-regression check. Any
failure leaves the document untouched.

### `--strip-broken-tags`

LOSSY: leaked close tags rendered as text (&lt;/p&gt; without &lt;).

Remove leaked HTML closing tags that are missing their open brackets (`</p>`
rendered as raw text in the reader).

### `--strip-watermarks`

LOSSY: producer stamps (OceanofPDF ...) and marker files.

Remove known producer and distributor watermarks (OceanofPDF, ABC Amber LIT
Converter, and the like) and stray marker files. The pass locates the stamp and
deletes the outermost wrapper whose entire visible text is the stamp, so prose
that merely mentions the URL is preserved.

When the stamp link sits inline, the bare `<a>` element is deleted under one of
two conditions: it is the only `<a>` in the match and its body is tag-free text
no longer than a stamp (120 normalized characters), whatever that text says; or
its visible text is exactly the watermark. Anything larger, such as an unclosed
stamp anchor that swallowed prose, is refused, counted, and reported for manual
repair rather than deleted.

### `--strip-stub-docs`

LOSSY: repeated placeholder spine docs, full manifest/NCX cascade.

Drop spine documents whose entire visible body text is one identical short
placeholder repeated across the spine: the Bookmate-style export whose chapters
are all the same "content unavailable" notice, which passes epubcheck because
the placeholder is valid XHTML.

The drop cascades through the archive entries, manifest items, spine order, NCX
navPoints, nav toc entries, and the OPF guide references naming a dropped file.
The dropped files also count as absent to the broken-reference machinery, so
when `--prune-missing-resources` runs in the same pass, a surviving document's
link into a dropped stub is stripped, with its anchor text kept, instead of
dangling. The book then opens straight into its real chapters.

The identity rule is deliberately conservative: the same body text in at least
3 documents covering at least 30% of the spine, each short but not blank, with
the pool taken from the `<body>` span. Pages that carry an `<img>` or `<image>`
are exempt whatever short text they hold, since a cover or an illustrated
divider is never a stub however it is stamped.

A book whose every spine doc is the stub is refused outright: that book is
empty and needs a re-source, never a repair.
