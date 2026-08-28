# The work tracker

Work items for one repository, in the rig's own database, reachable from the window and
from an agent over MCP.

## Why it is here

An agent that works in a repository has no memory between sessions. It repeats work it
already did, and it cannot tell what it left half finished. A `TODO.md` does not answer
this: it does not dedupe, it does not survive a branch change, and two agents overwrite
each other in it.

The rig already knows every repository, and it already keeps a stable identity for a
problem so that the same problem is one row across re-reviews. The tracker is that store,
opened to the agent.

## What it is not

It is not a replacement for GitHub Issues, Linear, or Jira. There are no assignees, no
boards, no cycles, and no epics across repositories. Nothing is synchronised anywhere.
This is scratch memory for the work in one checkout, and the team's tracker stays the
team's tracker.

## One list

A task and a review finding are the same row. The `source` says who wrote it:

| Source | Who wrote it |
| --- | --- |
| `model` | A review by the model. |
| `semgrep` | The security scan. |
| `agent` | An agent, through the tracker. |
| `person` | You, in the window. |

A state is one of `open`, `doing`, `done`, or `dropped`. The window shows `open` and
`doing` together, because both mean unfinished.

A category says what kind of problem it is: `security`, `correctness`, `performance`,
`quality`, `style`, or `task`. The reviewer names its own, the scan is security by
definition, and anything the tracker records is a task. The map filters on it.

## Attaching it to an agent

The tracker speaks MCP over stdin and stdout. It opens no port and it holds no token, so
nothing else on the machine can reach it, and it works when the Auger window is
closed.

In a checkout of this repository:

```json
{
  "mcpServers": {
    "auger": { "command": "auger", "args": ["tracker"] }
  }
}
```

From the packaged application:

```json
{
  "mcpServers": {
    "auger": {
      "command": "/Applications/Auger.app/Contents/Resources/engine/auger",
      "args": ["tracker"]
    }
  }
}
```

It takes the repository from the directory it starts in. Pass `--repo` to name another
one, and `--home` to read another Auger home.

## The tools

| Tool | What it does |
| --- | --- |
| `search` | Find items by words in the title and the detail. |
| `record` | Record one piece of work. |
| `note` | Append to an item's journal. |
| `set_state` | Move an item to `open`, `doing`, `done`, or `dropped`. |
| `list_open` | Every unfinished item, most severe first. |

`record` is the one that matters. The same work recorded twice returns the item that is
already there, with its journal, rather than a second copy. That is the answer to "have I
done this before", and the journal says what happened last time.

A note is append only. A journal that can be rewritten is not a record of anything.

## What to know before you rely on it

The state lives in the rig, not in the repository, so `git clone` does not carry it and a
fresh machine starts empty.

Text that an agent writes can reach a review prompt later. It is data there, under the
same rule that governs every tool result: it never becomes an instruction.

The window reads the database when you open a view. An item that an agent writes while
the window is open appears when the view next loads.
