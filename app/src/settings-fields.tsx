/** Inputs that write one setting back when the user leaves the field. */

import { Input, Select, SelectContent, SelectItem, SelectTrigger, SelectValue, Switch } from "@metaphor-cloud/ui";
import { useEffect, useState } from "react";

export function TextSetting({
  value,
  placeholder,
  className,
  onSave,
}: {
  value: string;
  placeholder?: string;
  className?: string;
  onSave: (next: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <Input
      className={className}
      value={draft}
      placeholder={placeholder}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => draft !== value && onSave(draft)}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
      }}
    />
  );
}

export function NumberSetting({
  value,
  suffix,
  onSave,
}: {
  value: number;
  suffix?: string;
  onSave: (next: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  return (
    <span className="flex items-center gap-2">
      <Input
        type="number"
        className="w-24"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          const next = Number(draft);
          if (Number.isFinite(next) && next !== value) onSave(next);
          else setDraft(String(value));
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
        }}
      />
      {suffix && <span className="text-xs text-text-secondary">{suffix}</span>}
    </span>
  );
}

export function SwitchSetting({
  checked,
  disabled,
  note,
  onSave,
}: {
  checked: boolean;
  disabled?: boolean;
  note?: string;
  onSave: (next: boolean) => void;
}) {
  return (
    <span className="flex items-center gap-2">
      <Switch checked={checked} disabled={disabled} onCheckedChange={onSave} />
      {note && <span className="text-xs text-text-secondary">{note}</span>}
    </span>
  );
}

export function ChoiceSetting<T extends string>({
  value,
  options,
  width = "w-40",
  onSave,
}: {
  value: T;
  options: readonly T[];
  width?: string;
  onSave: (next: T) => void;
}) {
  return (
    <Select value={value} onValueChange={(next) => onSave(next as T)}>
      <SelectTrigger className={width}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option} value={option}>
            {option}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
