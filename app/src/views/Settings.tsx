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
import EverySetting from "../parts/EverySetting";
import PromptEditor from "../parts/PromptEditor";
import { Block, Group, Row, SearchProvider } from "../settings/parts";
import { ChoiceSetting, NumberSetting, SwitchSetting, TextSetting } from "../settings-fields";
import type { Forge, McpServer, Mode, Root, Settings, SetupProgress, System } from "../types";
import Models from "./Models";
import SystemView from "./System";
import { Mono, PageTitle } from "../ui";

const SECTIONS = [
  { id: "where", label: "Repositories" },
  { id: "review", label: "Review" },
  { id: "models", label: "Models" },
  { id: "integrations", label: "Integrations" },
  { id: "system", label: "System" },
  { id: "advanced", label: "Advanced" },
] as const;

const MODES: readonly Mode[] = ["off", "draft", "complete"] as const;
const TRANSPORTS = ["stdio", "http"] as const;

/** Every polling interval, named. A key with no entry here used to reach the screen
 *  as raw snake case, which is how `verify_poll_seconds` ended up on show. */
const POLLS = [
  { key: "poll_seconds", label: "Check for new commits", help: "Seconds between asking each repository whether anything changed." },
  { key: "forge_poll_seconds", label: "Check pull requests", help: "Seconds between asking the connected forges for work assigned to you." },
  { key: "audit_poll_seconds", label: "Check for due audits", help: "Seconds between looking for a repository whose full audit is due." },
  { key: "model_poll_seconds", label: "Check model servers", help: "Seconds between asking the model servers whether they still answer." },
  { key: "verify_poll_seconds", label: "Check for findings to judge", help: "Seconds between looking for findings nobody has checked yet." },
  { key: "retry_seconds", label: "Retry a busy repository", help: "Seconds to wait before trying a repository that was busy or had no model." },
] as const;

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
  const [query, setQuery] = useState("");

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

      <Tabs
        defaultValue="where"
        orientation="vertical"
        className="flex flex-row items-start gap-6"
      >
        <div className="sticky top-0 w-40 shrink-0 space-y-3">
          <Input
            value={query}
            placeholder="Search settings"
            className="h-7 text-xs"
            onChange={(event) => setQuery(event.target.value)}
          />
          <TabsList className="flex h-auto w-full flex-col items-stretch gap-0.5 bg-transparent p-0">
            {SECTIONS.map((one) => (
              <TabsTrigger
                key={one.id}
                value={one.id}
                className="justify-start px-2.5 py-1.5 text-xs data-[state=active]:bg-bg-selected"
              >
                {one.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>

        <SearchProvider query={query}>
        <div className="min-w-0 flex-1">

        <TabsContent value="models">
          <Models setup={setup} nested />

          <Group
            title="Generation"
            description="How the reviewer is asked to answer. Leave these alone unless an answer is coming back wrong."
            keywords="tokens temperature profile sampling"
          >
            <Row
              label="Response limit"
              help="The longest answer the model may write, in tokens. 0 lets it finish; a small number cuts the findings off mid-answer."
              keywords="max_tokens"
            >
              <NumberSetting
                value={Number(settings.profile_limits?.review_max_tokens ?? 0)}
                title="tokens"
                onSave={(next) =>
                  void save(
                    `profile.${quoted(settings.defaults.model_profile)}.review.max_tokens`,
                    next,
                  )
                }
              />
            </Row>
            <Row
              label="Temperature"
              help="0 makes the model pick its likeliest answer every time, which is what a structured review wants."
            >
              <NumberSetting
                value={Number(settings.profile_limits?.review_temperature ?? 0)}
                onSave={(next) =>
                  void save(
                    `profile.${quoted(settings.defaults.model_profile)}.review.temperature`,
                    next,
                  )
                }
              />
            </Row>
            {(settings.profile_limits?.names.length ?? 0) > 1 && (
              <Row
                label="Model profile"
                help="Which set of model assignments to use. A job asks for a role, and the profile decides which server answers."
              >
                <ChoiceSetting
                  value={settings.defaults.model_profile}
                  options={settings.profile_limits?.names ?? []}
                  onSave={(model_profile) => void change("defaults", "", { model_profile })}
                />
              </Row>
            )}
          </Group>

          <Group
            title="Access"
            description="Where weights come from, and whether your code may leave this machine."
            keywords="hugging face token hosted egress"
          >
            <Row
              label="Hugging Face token"
              help="The name of an environment variable holding your token. Auger reads the variable, never the value in this file."
              keywords="token_env"
            >
              <TextSetting
                className="w-44"
                value={settings.models_token_env}
                placeholder="HF_TOKEN"
                onSave={(next) => void save("models.token_env", next)}
              />
            </Row>
            <Row
              label="Allow hosted models"
              help="Off by default. A hosted model means your code is sent to somebody else's server, and the backend must be marked hosted as well."
              keywords="allow_hosted"
            >
              <SwitchSetting
                checked={settings.allow_hosted}
                onSave={(next) => void save("egress.allow_hosted", next)}
              />
            </Row>
          </Group>
        </TabsContent>

        <TabsContent value="system">
          <SystemView system={system} nested />
        </TabsContent>

        {/* ---------------------------------------------------------------- review */}
        <TabsContent value="review">
          <Group
            title="What gets reviewed"
            description="Applies to every repository. An override below takes precedence."
            keywords="defaults policy"
          >
            <Row
              label="Pull request reviews"
              help="Off reviews nothing. Draft leaves a review for you to submit. Complete posts it under your name."
              keywords="mode"
            >
              <ChoiceSetting
                value={settings.defaults.mode}
                options={MODES}
                onSave={(mode) => void change("defaults", "", { mode })}
              />
            </Row>
            <Row
              label="Review pull requests assigned to you"
              help="Auger watches the forges you have connected and reviews what lands on your plate."
            >
              <SwitchSetting
                checked={settings.defaults.auto_review_assigned_prs}
                onSave={(next) => void change("defaults", "", { auto_review_assigned_prs: next })}
              />
            </Row>
            <Row
              label="Full repository audit"
              help="How many hours between reading a whole repository rather than a single change. 0 never does."
              keywords="audit_hours"
            >
              <NumberSetting
                value={settings.defaults.audit_hours}
                title="hours"
                onSave={(audit_hours) => void change("defaults", "", { audit_hours })}
              />
            </Row>
            <Row
              label="Wait after the last edit"
              help="Seconds a repository must sit still before it is reviewed, so you are not reviewed mid-keystroke."
              keywords="idle_seconds"
            >
              <NumberSetting
                value={settings.defaults.idle_seconds}
                title="seconds"
                onSave={(idle_seconds) => void change("defaults", "", { idle_seconds })}
              />
            </Row>
            <Row
              label="Queue priority"
              help="Which repositories go first when several are waiting. 1 is first, 9 is last."
            >
              <NumberSetting
                value={settings.defaults.priority}
                onSave={(priority) => void change("defaults", "", { priority })}
              />
            </Row>
          </Group>

          <Group
            title="Second opinion"
            description="A second model reads the findings and throws out the ones it cannot stand up."
            keywords="adversary verify judge"
          >
            <Row
              label="Check findings with a second model"
              help="Needs a second model set up under Models. It runs when the queue is quiet."
            >
              <SwitchSetting
                checked={settings.defaults.adversary}
                onSave={(adversary) => void change("defaults", "", { adversary })}
              />
            </Row>
            <Row
              label="Swap the two models each run"
              help="The reviewer and the checker trade places, so one model's blind spots do not decide alone."
              keywords="alternate"
            >
              <SwitchSetting
                checked={settings.defaults.alternate}
                onSave={(alternate) => void change("defaults", "", { alternate })}
              />
            </Row>
          </Group>

          <Group
            title="System prompt"
            description={
              <>
                What the reviewer is told, word for word. Start from a preset or write your
                own. A repository&apos;s <code>hints</code> are separate and are read as
                information, not as instructions.
              </>
            }
          >
            <PromptEditor
              rules={settings.defaults.system_prompt}
              onSave={(next) => void change("defaults", "", { system_prompt: next })}
            />
          </Group>

          <Group
            title="Overrides"
            description="Settings for one organisation or one repository, on top of the defaults above."
          >
            {settings.levels.length === 0 ? (
              <p className="text-xs text-text-secondary">
                None. Add an <code>[org.&quot;host/name&quot;]</code> or{" "}
                <code>[repo.&quot;/path&quot;]</code> section under Advanced.
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
          </Group>
        </TabsContent>

        {/* ----------------------------------------------------------------- where */}
        <TabsContent value="where">
          <Group
            title="Where to look"
            description="Auger walks each of these directories. A folder holding .git is a repository, and the walk stops there."
          >
            {settings.roots.length === 0 && (
              <p className="mb-3 text-xs text-text-secondary">
                No folder yet. Add one and Auger will find the repositories inside it.
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
                  <div className="mt-2 flex items-start justify-between gap-6">
                    <div className="min-w-0 flex-1">
                      <p className="text-xs text-text-primary">Depth</p>
                      <p className="mt-0.5 text-[11px] leading-snug text-text-secondary">
                        How many folders below this one to walk. 0 walks all the way down.
                      </p>
                    </div>
                    <NumberSetting
                      value={one.max_depth ?? 0}
                      title="levels below the root"
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
          </Group>

          <Group
            title="Skipped repositories"
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
          </Group>
        </TabsContent>

        {/* ----------------------------------------------------------------- tools */}
        <TabsContent value="integrations">
                  <Group
            title="Forges"
            description="A connected forge is added to the network allowlist, and its pull requests become reviewable."
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
              <code>glab</code>. A token is never written to the config file.
            </p>
          </Group>
                  <Group
            title="MCP servers"
            description="A server runs outside the sandbox and acts as you. What it returns is read as information, never as instructions."
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
                    <TableHead>Timeout</TableHead>
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
                        <TableCell>
                          <NumberSetting
                            value={Number(entry.timeout_seconds ?? 30)}
                            title="seconds"
                            onSave={(next) =>
                              void save(`mcp.${quoted(entry.name)}.timeout_seconds`, next)
                            }
                          />
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-right">
                          {live?.needs_sign_in && (
                            <Button
                              size="sm"
                              variant="secondary"
                              title="Opens your browser. Nothing else in Auger ever does."
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
          </Group>

          <Group
            title="Tool access"
            description="A review can call a tool only when it is named here."
          >
            <Row label="Allowed now" help="What a review can reach with the current list.">
              <Mono>{allowed.length ? allowed.join(", ") : "nothing"}</Mono>
            </Row>
            <Block
              label="Allow these"
              help="One tool as server.tool, or every tool on a server as server.*, separated by commas."
              keywords="tools allowlist"
            >
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
            </Block>
          </Group>
        </TabsContent>

        <TabsContent value="advanced">
          <Group
            title="When Auger works"
            description="Reviewing holds two cores and tens of gigabytes. These decide when it is allowed to."
            keywords="schedule idle quiet hours concurrency"
          >
            <Row
              label="Only work when you are away"
              help="Nothing is reviewed while you are at the keyboard, so the fans stay down while you work."
              keywords="idle_only"
            >
              <SwitchSetting
                checked={settings.schedule.idle_only as boolean}
                onSave={(next) => void save("schedule.idle_only", next)}
              />
            </Row>
            <Row
              label="Count as away after"
              help="Seconds the machine must be untouched before Auger treats you as gone."
              keywords="idle_after_seconds"
            >
              <NumberSetting
                value={Number(settings.schedule.idle_after_seconds)}
                title="seconds"
                onSave={(next) => void save("schedule.idle_after_seconds", next)}
              />
            </Row>
            <Row
              label="Quiet hours"
              help="No full audits during these hours. Leave empty for none."
              keywords="quiet_hours"
            >
              <TextSetting
                className="w-44"
                value={String(settings.schedule.quiet_hours ?? "")}
                placeholder="09:00-18:00"
                onSave={(next) => void save("schedule.quiet_hours", next)}
              />
            </Row>
            <Row
              label="Reviews at once"
              help="More finishes the queue sooner and takes more of the machine."
              keywords="max_concurrent_reviews"
            >
              <NumberSetting
                value={Number(settings.schedule.max_concurrent_reviews)}
                onSave={(next) => void save("schedule.max_concurrent_reviews", next)}
              />
            </Row>
          </Group>

          <Group
            title="How often Auger checks"
            description="Polling intervals. The defaults suit a machine you work on all day."
            keywords="poll interval seconds timing"
          >
            {POLLS.map((one) => (
              <Row key={one.key} label={one.label} help={one.help} keywords={one.key}>
                <NumberSetting
                  value={Number(settings.schedule[one.key])}
                  title="seconds"
                  onSave={(next) => void save(`schedule.${one.key}`, next)}
                />
              </Row>
            ))}
          </Group>

          <Group
            title="Call graph"
            description="A real call graph tells the reviewer who calls a symbol that changed."
            keywords="codegraph retrieval"
          >
            <Row
              label="Use CodeGraph"
              help={
                settings.codegraph_available
                  ? "Where a repository has an index, changed symbols come with their callers."
                  : "codegraph is not installed on this machine."
              }
            >
              <SwitchSetting
                checked={settings.codegraph}
                disabled={!settings.codegraph_available}
                onSave={(next) =>
                  void setCodegraph(next)
                    .then(setSettings)
                    .catch((cause) => setError(String(cause)))
                }
              />
            </Row>
          </Group>

          <Group
            title="Config file"
            description="The whole file, including anything no form covers. A file that fails to parse is not saved."
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
          </Group>
                  <p className="mb-3 text-xs text-text-secondary">
            Every setting there is, listed straight from the engine. The sections above
            group the ones people reach for. This one leaves nothing out.
          </p>
          <EverySetting version={version} onSave={save} />
        </TabsContent>
        </div>
        </SearchProvider>
      </Tabs>
    </>
  );
}
