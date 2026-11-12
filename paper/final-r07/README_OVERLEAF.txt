NET470 IEEE OVERLEAF R07
========================

Upload every file and the figures directory to a new Overleaf project.
Set main.tex as the main document and compile with pdfLaTeX.

This revision is bound to the frozen 125-episode five-level evaluation.
Primary dataset SHA-256:
B7939B0FFE443AD76AC5ECE693CDFC03015C735895D27835E2DE2AF1A87781BD

The manuscript uses exactly three primary comparative figures:
1. Recovery behavior with confidence intervals and explicit censoring.
2. SLO-violation duration with confidence intervals.
3. Replica-seconds with confidence intervals.

The paper intentionally does not claim C5 performance superiority. It reports
the observed limitations, T7-T8 dominance, and overload replica ceiling.

Compile twice so all figure and table references resolve. The final PDF must
visibly contain all three figures and must not contain "Figure ??" or blank
trailing pages.

R04 uses 0.75-inch academic margins to improve readability and target the
faculty-required 6-7 page range without adding filler or changing results.

R05 restores two evidence-bearing tables from the earlier paper structure,
using only the frozen 125-episode data. Stale 50-episode and PREWARM tables
are intentionally excluded.

R06 also restores the operational T0-T8 boundary table and the C5 production
decision/restoration table. These definitions remain valid and add substantive
method detail needed for the faculty-required 6-7 page manuscript.

R07 introduces a deliberate page boundary before the conclusion so the
conclusion, reproducibility statement, and references remain readable on a
content-bearing final page. This targets exactly six pages without filler.
