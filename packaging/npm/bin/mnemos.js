#!/usr/bin/env node
// npx mnemos-hub <init|serve|stdio|token|hygiene> [...]: runs the Python hub (PyPI mnemos-hub) through uv.
"use strict";
const { spawnSync } = require("node:child_process");
const version = require("../package.json").version;
const args = process.argv.slice(2);
const r = spawnSync("uvx", ["--from", `mnemos-hub==${version}`, "mnemos-local", ...(args.length ? args : ["--help"])],
  { stdio: "inherit" });
if (r.error && r.error.code === "ENOENT") {
  console.error("mnemos-hub needs uv (https://docs.astral.sh/uv/). On a Mac, the Mnemos app needs nothing:\n" +
    "https://github.com/ngentile92/mnemos/releases/latest/download/Mnemos-macos.dmg");
  process.exit(127);
}
process.exit(r.status ?? 1);
