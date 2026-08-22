# Bindery Library Audit & Repair Report

**Date:** 2026-08-22
**Library:** `~/docs/Calibre Library`
**Total EPUBs Audited:** 5,043
**Candidates for Repair:** ~3,228

## Context & Performance Breakthrough
Initially, scanning the entire library took an estimated **6.5 hours** due to the JVM startup penalty (`subprocess.run(["epubcheck"])` per file). To mitigate this, we implemented a dual-strategy optimization:

1. **Standalone Java `FastSweep`:** A custom multithreaded Java harness evaluated the entire 5,000-book library in just **10 minutes**.
2. **Transparent Daemonization:** We injected a persistent `EpubcheckDaemon` directly into `Bindery`'s Python core (`validate.py`). It automatically compiles a Java daemon and pipes EPUB paths over `stdin`, bypassing the JVM startup overhead entirely. 
   - **Result:** Single-file validations dropped from ~5.0s to ~0.05s.
   - **Impact:** The entire repair phase across the 3,228 broken books now completes in ~17 minutes instead of 4.5 hours.

## Recurring `epubcheck` Errors & Warnings
A deep inspection of the `epubcheck --json` output across the library's broken files revealed several commonly recurring schemas and structural violations that Bindery does not yet fix:

### High Frequency
1. **`[ERROR] value of attribute "id" is invalid; must be an XML name without colons`** (14x frequency in sample)
   - *Cause:* EPUB elements using colons in their `id` attributes (e.g., `id="foo:bar"`), which violates XML NCName rules.

### Medium Frequency
2. **`[ERROR] element "img" missing required attribute "alt"`** (5x frequency in sample)
   - *Cause:* Images missing the mandatory accessibility `alt` tag. Bindery already has some logic for this, but it may need broadening or enabling by default.
3. **`[ERROR] element "span" not allowed here; expected element ...`** (4x frequency in sample)
   - *Cause:* Block-level tags (`<div>`, `<p>`) nested inside inline elements (`<span>`), violating HTML5 structure.
4. **`[ERROR] element "body" incomplete; expected element ...`** (4x frequency in sample)
   - *Cause:* Empty `<body>` tags without any structural child tags.

### Low Frequency / Structural
5. **`[WARNING] The "head" element should have a "title" child element.`**
   - *Cause:* Missing `<title>` inside the `<head>` of XHTML documents.
6. **`[ERROR] identical playOrder values for navPoint/navTarget/pageTarget`**
   - *Cause:* Duplicate NCX `playOrder` attributes, which breaks table-of-contents navigation on older e-readers.
7. **`[ERROR] attribute "value" not allowed here; expected attribute "class", "dir", "id", "lang", "style", "title" or "xml:lang"`**
   - *Cause:* Invalid HTML attributes injected by poor conversion tools (often found on `<li>` or similar tags).

---
*Note: The actual `bindery-report.json` was generated alongside the repair phase, tracking the exact before/after counts of every single file modified.*
