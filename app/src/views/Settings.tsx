import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  Input,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Textarea,
} from "@metaphor-cloud/ui";
import { useCallback, useEffect, useState } from "react";

import {
  changeExclusion,
  changeSettings,
  checkTools,
  getConfigText,
  getForges,
  getSettings,
  getTools,
  setCodegraph,
  setSetting,
  signInTool,
  writeConfigText,
} from "../engine";
import PromptEditor from "../parts/PromptEditor";
import { ChoiceSetting, NumberSetting, SwitchSetting, TextSetting } from "../settings-fields";
import type { Forge, McpServer, Mode, Root, Settings, SetupProgress, System } from "../types";
import Models from "./Models";
import SystemView from "./System";
import { Fact, Facts, Mono, PageTitle, Section } from "../ui";

const MODES: readonly Mode[] = ["off", "draft", "complete"] as const;
const TRANSPORTS = ["stdio", "http"] as const;

const SCHEDULE_LABEL: Record<string, string> = {
  max_concurrent_reviews: "Reviews at once",
  poll_seconds: "Look for a new commit every",
  forge_poll_seconds: "Ask the forges every",
  retry_seconds: "Retry a busy repository after",
  audit_poll_seconds: "Look for a due audit every",
  model_poll_seconds: "Check the models every",
  quiet_hours: "No audit during",
};

/** A config key holds dots of its own, so it goes into the path quoted. */
function quoted(key: string) {
  return JSON.stringify(key);
}

type NewServer = {
  name: string;
  transport: (typeof TRANSPORTS)[number];
  target: string;
  oauth: boolean;
};

const EMPTY_SERVER: NewServer = { name: "", transport: "stdio", target: "", oauth: false };

export default function SettingsView({
  version,
  setup,
  system,
}: {
  version: number;
  setup: SetupProgress | null;
  system: System | null;
}) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [forges, setForges] = useState<Forge[]>([]);
  const [servers, setServers] = useState<McpServer[]>([]);
  const [allowed, setAllowed] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pattern, setPattern] = useState("");
  const [root, setRoot] = useState("");
  const [server, setServer] = useState<NewServer>(EMPTY_SERVER);
  const [raw, setRaw] = useState<string | null>(null);
  const [rawSaved, setRawSaved] = useState(true);

  const load = useCallback(async () => {
    try {
      const body = await getSettings();
      setSettings(body);
      setForges((await getForges()).forges);
      const tools = await getTools();
      setServers(tools.servers);
      setAllowed(tools.allowed);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, version]);

  const save = useCallback(async (path: string, value: unknown, remove = false) => {
    try {
      setSettings(await setSetting(path, value, remove));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  async function change(
    level: "defaults" | "org" | "repo",
    key: string,
    changes: Record<string, unknown>,
  ) {
    try {
      setSettings(await changeSettings(level, key, changes));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function saveRoots(roots: Root[]) {
    await save(
      "roots",
      roots.map((one) => ({
        path: one.path,
        exclude: one.exclude,
        ...(one.max_depth === null ? {} : { max_depth: one.max_depth }),
      })),
    );
  }

  async function addServer() {
    const words = server.target.trim().split(/\s+/);
    const body =
      server.transport === "stdio"
        ? { enabled: true, transport: "stdio", command: words[0], args: words.slice(1) }
        : {
            enabled: true,
            transport: "http",
            url: server.target.trim(),
            auth: server.oauth ? "oauth" : "none",
          };
    await save(`mcp.${quoted(server.name.trim())}`, body);
    setServer(EMPTY_SERVER);
    setServers((await getTools()).servers);
  }

  async function openRaw() {
    try {
      setRaw(await getConfigText());
      setRawSaved(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function saveRaw() {
    if (raw === null) return;
    try {
      setSettings(await writeConfigText(raw));
      setRawSaved(true);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  if (settings === null) {
    return <p className="text-xs text-text-secondary">{error ?? "Loading"}</p>;
  }

  const complete = settings.levels.filter((level) => level.overrides.mode === "complete");
  const defaultsComplete = settings.defaults.mode === "complete";

  return (
    <>
      <PageTitle title="Settings" description={<Mono>{settings.config_path}</Mono>} />

      {error && (
        <Alert variant="danger" className="mb-4">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {(complete.length > 0 || defaultsComplete) && (
        <Alert variant="warning" className="mb-4">
          <AlertDescription>
            Complete mode submits comments on real pull requests under your name:{" "}
            {defaultsComplete ? "every repository" : complete.map((one) => one.key).join(", ")}.
          </AlertDescription>
        </Alert>
      )}

      <Tabs defaultValue="where">
        <TabsList className="mb-4">
          <TabsTrigger value="where">Where to look</TabsTrigger>
          <TabsTrigger value="models">Models</TabsTrigger>
          <TabsTrigger value="review">Review</TabsTrigger>
          <TabsTrigger value="tools">Tools</TabsTrigger>
          <TabsTrigger value="forges">Forges</TabsTrigger>
          <TabsTrigger value="system">System</TabsTrigger>
          <TabsTrigger value="advanced">Advanced</TabsTrigger>
        </TabsList>

        <TabsContent value="models">
          <Models setup={setup} nested />
        </TabsContent>

        <TabsContent value="system">
          <SystemView system={system} nested />
        </TabsContent>

        {/* ---------------------------------------------------------------- review */}
        <TabsContent value="review">
          <Section title="All repositories" description="What every repository gets, unless it overrides it.">
            <Facts>
              <Fact label="Mode">
                <ChoiceSetting
                  value={settings.defaults.mode}
                  options={MODES}
                  onSave={(mode) => void change("defaults", "", { mode })}
                />
              </Fact>
              <Fact label="Review pull requests assigned to me">
                <SwitchSetting
                  checked={settings.defaults.auto_review_assigned_prs}
                  onSave={(next) =>
                    void change("defaults", "", { auto_review_assigned_prs: next })
                  }
                />
              </Fact>
              <Fact label="Priority">
                <NumberSetting
                  value={settings.defaults.priority}
                  suffix="1 first, 9 last"
                  onSave={(priority) => void change("defaults", "", { priority })}
                />
              </Fact>
              <Fact label="Wait after an agent stops">
                <NumberSetting
                  value={settings.defaults.idle_seconds}
                  suffix="seconds"
                  onSave={(idle_seconds) => void change("defaults", "", { idle_seconds })}
                />
              </Fact>
              <Fact label="Audit a whole repository every">
                <NumberSetting
                  value={settings.defaults.audit_hours}
                  suffix="hours, 0 turns it off"
                  onSave={(audit_hours) => void change("defaults", "", { audit_hours })}
                />
              </Fact>
              <Fact label="Tool calls one review may make">
                <NumberSetting
                  value={settings.defaults.max_tool_calls}
                  onSave={(max_tool_calls) => void change("defaults", "", { max_tool_calls })}
                />
              </Fact>
              <Fact label="Model profile">
                <TextSetting
                  className="w-40"
                  value={settings.defaults.model_profile}
                  onSave={(model_profile) => void change("defaults", "", { model_profile })}
                />
              </Fact>
            </Facts>
          </Section>

          <Section
            title="What to look for"
            description={
              <>
                Pick a set, or write your own. It narrows what the reviewer reports, adds
                something to look for, or changes how it judges severity. A repository&apos;s
                own <code>hints</code> are separate, and are treated as data.
              </>
            }
          >
            <PromptEditor
              instructions={settings.defaults.instructions}
              onSave={(next) => void change("defaults", "", { instructions: next })}
            />
          </Section>

          <Section
            title="Overrides"
            description="One organisation or one repository, over the settings above."
          >
            {settings.levels.length === 0 ? (
              <p className="text-xs text-text-secondary">
                None. Add an <code>[org.&quot;host/name&quot;]</code> or{" "}
                <code>[repo.&quot;/path&quot;]</code> section in Advanced.
              </p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Level</TableHead>
                    <TableHead>Applies to</TableHead>
                    <TableHead>Mode</TableHead>
                    <TableHead>Priority</TableHead>
                    <TableHead>Assigned pull requests</TableHead>
                    <TableHead>Other</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {settings.levels.map((level) => (
                    <TableRow key={`${level.level}:${level.key}`}>
                      <TableCell>{level.level}</TableCell>
                      <TableCell className="max-w-[16rem] truncate">
                        <Mono title={level.key}>{level.key}</Mono>
                      </TableCell>
                      <TableCell>
                        <ChoiceSetting
                          value={(level.overrides.mode as Mode) ?? settings.defaults.mode}
                          options={MODES}
                          width="w-32"
                          onSave={(mode) => void change(level.level, level.key, { mode })}
                        />
                      </TableCell>
                      <TableCell>
                        <NumberSetting
                          value={
                            (level.overrides.priority as number) ?? settings.defaults.priority
                          }
                          onSave={(priority) =>
                            void change(level.level, level.key, { priority })
                          }
                        />
                      </TableCell>
                      <TableCell>
                        <SwitchSetting
                          checked={level.overrides.auto_review_assigned_prs !== false}
                          onSave={(next) =>
                            void change(level.level, level.key, {
                              auto_review_assigned_prs: next,
                            })
                          }
                        />
                      </TableCell>
                      <TableCell className="text-text-secondary">
                        <Mono>
                          {Object.keys(level.overrides)
                            .filter(
                              (name) =>
                                !["mode", "priority", "auto_review_assigned_prs"].includes(name),
                            )
                            .join(", ")}
                        </Mono>
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          size="sm"
                          variant="ghost"
                          title="Remove this override"
                          onClick={() =>
                            void save(
                              `${level.level === "org" ? "org" : "repo"}.${quoted(level.key)}`,
                              null,
                              true,
                            )
                          }
                        >
                          Remove
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Section>
        </TabsContent>

        {/* ----------------------------------------------------------------- where */}
        <TabsContent value="where">
          <Section
            title="Roots"
            description="Every directory the rig walks. A directory that holds .git is a repository, and the walk stops there."
          >
            {settings.roots.length === 0 && (
              <p className="mb-3 text-xs text-text-secondary">
                No root. The rig finds nothing until you add one.
              </p>
            )}
            <div className="space-y-3">
              {settings.roots.map((one, index) => (
                <div key={one.path} className="rounded-md border border-border-subtle p-3">
                  <div className="flex items-center gap-2">
                    <TextSetting
                      value={one.path}
                      onSave={(path) => {
                        const next = [...settings.roots];
                        next[index] = { ...one, path };
                        void saveRoots(next);
                      }}
                    />
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        void saveRoots(settings.roots.filter((_, other) => other !== index))
                      }
                    >
                      Remove
                    </Button>
                  </div>
                  <div className="mt-2 flex items-center gap-2">
                    <span className="w-28 shrink-0 text-xs text-text-secondary">Skip</span>
                    <TextSetting
                      value={one.exclude.join(", ")}
                      placeholder="**/fixtures/**, ~/git/archive"
                      onSave={(text) => {
                        const exclude = text
                          .split(",")
                          .map((entry) => entry.trim())
                          .filter(Boolean);
                        const next = [...settings.roots];
                        next[index] = { ...one, exclude };
                        void saveRoots(next);
                      }}
                    />
                  </div>
                  <div className="mt-2 flex items-center gap-2">
                    <span className="w-28 shrink-0 text-xs text-text-secondary">Depth</span>
                    <NumberSetting
                      value={one.max_depth ?? 0}
                      suffix="levels below the root, 0 means no limit"
                      onSave={(depth) => {
                        const next = [...settings.roots];
                        next[index] = { ...one, max_depth: depth > 0 ? depth : null };
                        void saveRoots(next);
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-3 flex items-center gap-2">
              <Input
                value={root}
                placeholder="~/git"
                onChange={(event) => setRoot(event.target.value)}
              />
              <Button
                disabled={!root.trim()}
                onClick={() => {
                  void saveRoots([
                    ...settings.roots,
                    { path: root.trim(), exclude: [], max_depth: null },
                  ]).then(() => setRoot(""));
                }}
              >
                Add root
              </Button>
            </div>
          </Section>

          <Section
            title="Excluded repositories"
            description={
              <>
                A path, a glob, or a forge key such as <code>github.com/acme</code>. An excluded
                repository stays listed and is never reviewed.
              </>
            }
          >
            {settings.exclude.length > 0 && (
              <ul className="mb-3 flex flex-wrap gap-2">
                {settings.exclude.map((entry) => (
                  <li
                    key={entry}
                    className="flex items-center gap-1 rounded-md border border-border-subtle px-2 py-1"
                  >
                    <Mono>{entry}</Mono>
                    <button
                      className="text-text-tertiary transition-colors hover:text-danger"
                      title="Stop excluding this"
                      onClick={() =>
                        void changeExclusion(entry, true)
                          .then(setSettings)
                          .catch((cause) => setError(String(cause)))
                      }
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex items-center gap-2">
              <Input
                value={pattern}
                placeholder="~/git/scratch"
                onChange={(event) => setPattern(event.target.value)}
              />
              <Button
                disabled={!pattern.trim()}
                onClick={() =>
                  void changeExclusion(pattern, false)
                    .then((body) => {
                      setSettings(body);
                      setPattern("");
                    })
                    .catch((cause) => setError(String(cause)))
                }
              >
                Exclude
              </Button>
            </div>
          </Section>
        </TabsContent>

        {/* ----------------------------------------------------------------- tools */}
        <TabsContent value="tools">
          <Section
            title="MCP servers"
            description="A server runs outside the sandbox and speaks for you. Nothing it returns is treated as an instruction."
            action={
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void checkTools().then((body) => setServers(body.servers))}
              >
                Check
              </Button>
            }
          >
            {settings.mcp.length === 0 ? (
              <p className="text-xs text-text-secondary">None attached.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Server</TableHead>
                    <TableHead>Runs</TableHead>
                    <TableHead>On</TableHead>
                    <TableHead>State</TableHead>
                    <TableHead>Tools</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {settings.mcp.map((entry) => {
                    const live = servers.find((one) => one.name === entry.name);
                    return (
                      <TableRow key={entry.name}>
                        <TableCell>{entry.name}</TableCell>
                        <TableCell className="max-w-[16rem] truncate">
                          <Mono title={entry.target}>{entry.target}</Mono>
                        </TableCell>
                        <TableCell>
                          <SwitchSetting
                            checked={entry.enabled}
                            onSave={(next) =>
                              void save(`mcp.${quoted(entry.name)}.enabled`, next)
                            }
                          />
                        </TableCell>
                        <TableCell>
                          <Badge variant={live?.reachable ? "success" : "outline"}>
                            {!entry.enabled
                              ? "off"
                              : live?.reachable
                                ? "ready"
                                : live?.needs_sign_in && !live.signed_in
                                  ? "sign in needed"
                                  : "unavailable"}
                          </Badge>
                        </TableCell>
                        <TableCell className="max-w-[18rem] truncate">
                          <Mono title={live?.reason ?? ""}>
                            {live?.reachable
                              ? live.tools.map((tool) => tool.name).join(", ")
                              : (live?.reason ?? "")}
                          </Mono>
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-right">
                          {live?.needs_sign_in && (
                            <Button
                              size="sm"
                              variant="secondary"
                              title="Opens your browser. Nothing else in the rig ever does."
                              onClick={() =>
                                void signInTool(entry.name)
                                  .then((body) => setServers(body.servers))
                                  .catch((cause) =>
                                    setError(cause instanceof Error ? cause.message : String(cause)),
                                  )
                              }
                            >
                              {live.signed_in ? "Sign in again" : "Sign in"}
                            </Button>
                          )}
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => void save(`mcp.${quoted(entry.name)}`, null, true)}
                          >
                            Remove
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}

            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Input
                className="w-40"
                value={server.name}
                placeholder="name"
                onChange={(event) => setServer({ ...server, name: event.target.value })}
              />
              <ChoiceSetting
                value={server.transport}
                options={TRANSPORTS}
                width="w-28"
                onSave={(transport) => setServer({ ...server, transport })}
              />
              <Input
                className="flex-1"
                value={server.target}
                placeholder={
                  server.transport === "stdio"
                    ? "npx -y @acme/mcp-server"
                    : "https://tools.example.com/mcp"
                }
                onChange={(event) => setServer({ ...server, target: event.target.value })}
              />
              {server.transport === "http" && (
                <SwitchSetting
                  checked={server.oauth}
                  note="OAuth"
                  onSave={(oauth) => setServer({ ...server, oauth })}
                />
              )}
              <Button
                disabled={!server.name.trim() || !server.target.trim()}
                onClick={() => void addServer()}
              >
                Attach
              </Button>
            </div>
            <p className="mt-2 text-xs text-text-secondary">
              A server sees only <code>PATH</code>, <code>HOME</code>, and the variables its{" "}
              <code>pass_env</code> names. Add <code>pass_env</code> in Advanced.
            </p>
          </Section>

          <Section
            title="Which tools a review may call"
            description="A tool runs only when a policy names it."
          >
            <Facts>
              <Fact label="Allowed by default">
                <Mono>{allowed.length ? allowed.join(", ") : "nothing"}</Mono>
              </Fact>
              <Fact label="Form">
                <Mono>server.tool, or server.*</Mono>
              </Fact>
            </Facts>
            <div className="mt-3">
              <TextSetting
                value={settings.defaults.tools.join(", ")}
                placeholder="linear.*, jira.search"
                onSave={(text) =>
                  void change("defaults", "", {
                    tools: text
                      .split(",")
                      .map((entry) => entry.trim())
                      .filter(Boolean),
                  })
                }
              />
            </div>
          </Section>
        </TabsContent>

        {/* ---------------------------------------------------------------- forges */}
        <TabsContent value="forges">
          <Section
            title="Forges"
            description="An enabled forge joins the egress allowlist, and the rig reads its pull requests."
          >
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Forge</TableHead>
                  <TableHead>Host</TableHead>
                  <TableHead>On</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead>Signed in as</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {settings.forges.map((entry) => {
                  const live = forges.find((one) => one.name === entry.name);
                  return (
                    <TableRow key={entry.name}>
                      <TableCell>{entry.name}</TableCell>
                      <TableCell>
                        <Mono>{entry.host}</Mono>
                      </TableCell>
                      <TableCell>
                        <SwitchSetting
                          checked={entry.enabled}
                          onSave={(next) =>
                            void save(`forge.${quoted(entry.name)}.enabled`, next).then(() =>
                              getForges().then((body) => setForges(body.forges)),
                            )
                          }
                        />
                      </TableCell>
                      <TableCell>
                        <Badge variant={live?.reachable ? "success" : "outline"}>
                          {!entry.enabled ? "off" : live?.reachable ? "connected" : "unavailable"}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-text-secondary" title={live?.reason ?? ""}>
                        {live?.user || live?.reason || ""}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
            <p className="mt-2 text-xs text-text-secondary">
              The token comes from the variable the config names, or from <code>gh</code> and{" "}
              <code>glab</code>. The rig never writes a token to its config.
            </p>
          </Section>
        </TabsContent>

        {/* -------------------------------------------------------------- advanced */}
        <TabsContent value="advanced">
          <Section title="Retrieval">
            <Facts>
              <Fact label="Use CodeGraph">
                <SwitchSetting
                  checked={settings.codegraph}
                  disabled={!settings.codegraph_available}
                  note={
                    settings.codegraph_available
                      ? "Asks a real call graph who calls a changed symbol, where a repository has an index."
                      : "codegraph is not installed."
                  }
                  onSave={(next) =>
                    void setCodegraph(next).then(setSettings).catch((cause) => setError(String(cause)))
                  }
                />
              </Fact>
            </Facts>
          </Section>

          <Section title="Hosted models">
            <Facts>
              <Fact label="Allow a hosted backend">
                <SwitchSetting
                  checked={settings.allow_hosted}
                  note="A hosted backend sends your code off this machine. The backend must also be marked hosted."
                  onSave={(next) => void save("egress.allow_hosted", next)}
                />
              </Fact>
            </Facts>
          </Section>

          <Section title="Schedule" description="How hard the rig works.">
            <Facts>
              {Object.entries(settings.schedule).map(([name, value]) => (
                <Fact key={name} label={SCHEDULE_LABEL[name] ?? name}>
                  {typeof value === "number" ? (
                    <NumberSetting
                      value={value}
                      suffix={name.endsWith("_seconds") ? "seconds" : ""}
                      onSave={(next) => void save(`schedule.${name}`, next)}
                    />
                  ) : (
                    <TextSetting
                      className="w-40"
                      value={String(value)}
                      placeholder="09:00-18:00"
                      onSave={(next) => void save(`schedule.${name}`, next)}
                    />
                  )}
                </Fact>
              ))}
            </Facts>
          </Section>

          <Section
            title="Config file"
            description="Everything, including the keys no form covers. A refused file is not written."
            action={
              raw === null ? (
                <Button size="sm" variant="secondary" onClick={() => void openRaw()}>
                  Open
                </Button>
              ) : (
                <div className="flex gap-2">
                  <Button size="sm" variant="ghost" onClick={() => setRaw(null)}>
                    Close
                  </Button>
                  <Button size="sm" disabled={rawSaved} onClick={() => void saveRaw()}>
                    {rawSaved ? "Saved" : "Save"}
                  </Button>
                </div>
              )
            }
          >
            {raw === null ? (
              <p className="text-xs text-text-secondary">
                <Mono>{settings.config_path}</Mono>
              </p>
            ) : (
              <Textarea
                rows={22}
                className="font-mono text-[11px]"
                value={raw}
                onChange={(event) => {
                  setRaw(event.target.value);
                  setRawSaved(false);
                }}
              />
            )}
          </Section>
        </TabsContent>
      </Tabs>
    </>
  );
}
