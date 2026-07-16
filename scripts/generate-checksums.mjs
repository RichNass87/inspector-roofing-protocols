import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const excludedDirectories = new Set([".git", "node_modules", "dist", "build"]);
const excludedFiles = new Set([
  "CHECKSUMS.sha256",
  "release-readiness-report.json",
  "release-readiness-report.md"
]);

function walk(directory) {
  const files = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.isDirectory() && excludedDirectories.has(entry.name)) continue;
    const absolute = join(directory, entry.name);
    const rel = relative(root, absolute).replaceAll("\\", "/");
    if (entry.isDirectory()) files.push(...walk(absolute));
    else if (entry.isFile() && !excludedFiles.has(rel)) files.push(rel);
  }
  return files;
}

const lines = walk(root)
  .sort((a, b) => a.localeCompare(b))
  .map(file => {
    const digest = createHash("sha256").update(readFileSync(join(root, file))).digest("hex");
    return `${digest}  ${file}`;
  });

writeFileSync(join(root, "CHECKSUMS.sha256"), `${lines.join("\n")}\n`, "utf8");
console.log(`wrote ${lines.length} SHA-256 entries to CHECKSUMS.sha256`);
