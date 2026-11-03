import { memo } from "react";

interface DiffViewProps {
  diff: string;
  compact?: boolean;
}

export const DiffView = memo(function DiffView({ diff, compact = false }: DiffViewProps) {
  if (!diff) return null;
  
  const lines = diff.split("\n");
  if (compact && lines.length > 30) {
    return (
      <pre className="diff">
        {lines.slice(0, 15).map((l, i) => (
          <span key={i} className={getLineClass(l)}>{l}</span>
        ))}
        <span className="meta">... {lines.length - 30} lines hidden ...</span>
        {lines.slice(-15).map((l, i) => (
          <span key={lines.length - 15 + i} className={getLineClass(l)}>{l}</span>
        ))}
      </pre>
    );
  }
  
  return (
    <pre className="diff">
      {lines.map((l, i) => (
        <span key={i} className={getLineClass(l)}>{l}{"\n"}</span>
      ))}
    </pre>
  );
});

function getLineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ") || line.startsWith("index ")) {
    return "meta";
  }
  if (line.startsWith("@@")) {
    return "hunk";
  }
  if (line.startsWith("+")) {
    return "add";
  }
  if (line.startsWith("-")) {
    return "del";
  }
  return "";
}

export function parseToolArgs(args: string): Record<string, unknown> {
  try {
    return JSON.parse(args);
  } catch {
    return {};
  }
}

export function extractDiffFromArgs(args: string): string | null {
  const parsed = parseToolArgs(args);
  if (parsed.diff && typeof parsed.diff === "string") {
    return parsed.diff;
  }
  if (parsed.old_text && parsed.new_text) {
    return generateUnifiedDiff(
      (parsed.path as string) || "file",
      parsed.old_text as string,
      parsed.new_text as string
    );
  }
  return null;
}

function generateUnifiedDiff(filename: string, oldText: string, newText: string): string {
  const oldLines = oldText.split("\n");
  const newLines = newText.split("\n");
  
  let diff = `diff --git a/${filename} b/${filename}\n`;
  diff += `--- a/${filename}\n`;
  diff += `+++ b/${filename}\n`;
  
  const maxLen = Math.max(oldLines.length, newLines.length);
  for (let i = 0; i < maxLen; i++) {
    const oldLine = oldLines[i];
    const newLine = newLines[i];
    if (oldLine !== newLine) {
      if (oldLine !== undefined) diff += `-${oldLine}\n`;
      if (newLine !== undefined) diff += `+${newLine}\n`;
    }
  }
  return diff;
}
