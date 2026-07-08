/**
 * @deprecated Use run_bdms_fetch.mjs — XHR path fails under jsdom CORS.
 * Thin wrapper for backward compatibility.
 */
import { spawnSync } from "child_process";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const fetchScript = path.join(__dirname, "run_bdms_fetch.mjs");
const args = [fetchScript, process.argv[2] || "", process.argv[3] ?? "", process.argv[4] || "GET"];

const result = spawnSync("node", args, {
  encoding: "utf-8",
  cwd: path.join(__dirname, ".."),
  stdio: ["inherit", "pipe", "pipe"],
});

if (result.stdout) process.stdout.write(result.stdout);
if (result.stderr) process.stderr.write(result.stderr);
process.exit(result.status ?? (result.error ? 1 : 0));
