/** Every setting, drawn from what the engine says it has.
 *
 * A setting only a text editor can reach is a setting most people never find. These
 * controls are built from the schema rather than by hand, so a key added to the engine
 * is reachable the day it is added, whether or not anybody remembered a form for it.
 *
 * The sections above this one are better: they group settings the way a person thinks
 * about them. This is the floor, not the ceiling.
 */

import { Alert, AlertDescription } from "@metaphor-cloud/ui";
import { useCallback, useEffect, useState } from "react";

import { getSettingsSchema } from "../engine";
import { ChoiceSetting, NumberSetting, SwitchSetting, TextSetting } from "../settings-fields";
import type { SettingField, SettingsSchema } from "../types";
import { Group, Row } from "../settings/parts";

/** `max_concurrent_reviews` -> `Max concurrent reviews`. A key is not a label, but a
 *  readable key beats a raw one on a page whose job is to leave nothing out. */
function sentence(key: string) {
  const words = key.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function Control({
  field,
  onSave,
}: {
  field: SettingField;
  onSave: (path: string, value: unknown) => void;
}) {
  if (field.kind === "boolean") {
    return (
      <SwitchSetting
        checked={field.value === true}
        note={field.describes}
        onSave={(next) => onSave(field.path, next)}
      />
    );
  }
  if (field.choices.length > 0) {
    return (
      <ChoiceSetting
        value={String(field.value ?? "")}
        options={field.choices}
        onSave={(next) => onSave(field.path, next)}
      />
    );
  }
  if (field.kind === "integer" || field.kind === "number") {
    return (
      <NumberSetting
        value={Number(field.value ?? 0)}
        suffix={field.key.endsWith("_seconds") ? "seconds" : ""}
        onSave={(next) => onSave(field.path, field.kind === "integer" ? Math.round(next) : next)}
      />
    );
  }
  return (
    <TextSetting
      className="w-72"
      value={String(field.value ?? "")}
      onSave={(next) => onSave(field.path, next)}
    />
  );
}

export default function EverySetting({
  version,
  onSave,
}: {
  version: number;
  onSave: (path: string, value: unknown) => Promise<void> | void;
}) {
  const [schema, setSchema] = useState<SettingsSchema | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSchema(await getSettingsSchema());
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, version]);

  async function save(path: string, value: unknown) {
    await onSave(path, value);
    await load();
  }

  return (
    <>
      {error && (
        <Alert variant="danger" className="mb-3">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {(schema?.sections ?? []).map((section) => (
        <Group key={section.name} title={section.title} description={section.describes}>
          {section.fields.map((field) => (
            <Row
              key={field.path}
              label={sentence(field.key)}
              help={field.describes}
              keywords={field.path}
            >
              <Control field={field} onSave={(path, value) => void save(path, value)} />
            </Row>
          ))}
        </Group>
      ))}
    </>
  );
}
