# The defect corpus

Each directory is one planted defect. `case.toml` holds the answer; `before/` and
`after/` hold the code the reviewer sees. The reviewer is shown the diff between them
and nothing else, so nothing in `case.toml` can leak into what it reads.

Tiers say how much understanding a defect takes to see, not how much damage it does.

1. Visible in the changed lines alone. A competent linter would argue for most of these.
2. Needs the rest of the function, or knowledge of one library's contract.
3. Needs to hold two parts of the file in mind at once: a lock and its release, a
   check and its use, an allocation and its bound.
4. Needs to know why the code is written the way it is. The change looks like an
   improvement, reads cleanly, passes its tests, and is wrong for a reason that lives
   in a protocol, a memory model, or a security property.

A case is worth keeping only if the `after` code is plausible. Code nobody would write
measures nothing.
