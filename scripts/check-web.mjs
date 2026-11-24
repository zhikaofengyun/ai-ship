import { readdirSync, readFileSync, statSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const files = [];
function scan(dir) {
  for (const name of readdirSync(dir)) {
    const path = resolve(dir, name);
    if (statSync(path).isDirectory()) scan(path);
    else if (name.endsWith(".js")) files.push(path);
  }
}
scan(resolve(root, "web"));
let failed = false;
for (const path of files) {
  const result = spawnSync(process.execPath, ["--check", path], {
    encoding: "utf8",
  });
  if (result.status !== 0) {
    failed = true;
    process.stderr.write(result.stderr);
  }
  const content = readFileSync(path, "utf8");
  for (const match of content.matchAll(/from\s+['"](\.\.?\/[^'"]+)['"]/g)) {
    try {
      statSync(resolve(dirname(path), match[1]));
    } catch {
      failed = true;
      process.stderr.write(`Missing import ${match[1]} in ${path}\n`);
    }
  }
}
console.log(
  JSON.stringify({
    files: files.length,
    passed: !failed,
    check: "JavaScript syntax and local module resolution",
  }),
);
process.exitCode = failed ? 1 : 0;
