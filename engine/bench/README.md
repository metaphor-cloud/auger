# Measuring a model

A model goes in the catalogue on a number, not on a claim from its card. This is where
the number comes from.

## Retrieval

`just bench <repository> <model> [model ...]` indexes one repository once per model into
a throwaway database, asks the same questions of each, and prints what each one found.

A question is one symbol: given its body, which other files does a reviewer need? The
answer key is the set of files that name that symbol, read from the chunk text rather
than from any search Auger runs. Scoring against Auger's own keyword search would score
the keyword search.

Three numbers come back.

- `recall@12` is the share of the answer key found in the first twelve files.
- `prec@5` is the share of the first five files that were in the answer key.
- `MRR` is one over the rank of the first correct file, averaged.

Below the table is the part that decides anything: the two models question by question.
An average over forty symbols moves on two lucky answers, so the counts of which model
answered each symbol better are what to read.

Only vector search is measured, not the rank fusion a review actually uses. That is
deliberate: fusion with keyword search narrows the gap between any two embedders, and
the question here is which embedder is better.

## Review quality

`just bench-review` plants a known defect in a throwaway git repository, one case at a
time, and runs the same `diff_review` job a real review runs. The model is shown the
diff and nothing else. Add `--tier 4` for only the hard ones, `--model <backend>` to
measure a model that is not the configured one, `--repeat 3` to see how much the answer
moves between runs.

Cases live in `cases/`, one directory each: `before/`, `after/`, and a `case.toml`
holding the answer. Tier 1 is visible in the changed lines; tier 4 needs to know why the
code was written the way it was, and reads as an improvement until you know.

Two numbers come back. Detection is the share of planted defects reported. Noise is the
findings per case that were not the planted defect. Read them together: a model that
reports everything detects everything and is worth nothing.

A finding counts when it points inside the planted span, give or take six lines. That is
blunt on purpose. Asking a second model whether two descriptions mean the same thing
would measure that model as well.

### Adding a case

The `after` code has to be code somebody would plausibly write, ideally something that
looks like an improvement. Code nobody would write measures nothing. Nothing in
`before/` or `after/` may hint at the answer: `tests/test_bench.py` checks that, checks
that every case's span covers a line that actually changed, and checks that all four
tiers are represented.
