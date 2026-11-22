#!/usr/bin/env node
// 跨平台调用本地 @tauri-apps/cli。
//
// 原 package.json 用 `PATH="$HOME/.cargo/bin:$PATH" tauri ...` 这种 POSIX 前缀写法，
// 在 Windows 的 cmd/PowerShell 下会被当成可执行文件名直接失败。这里用 Node 统一
// 注入 $HOME/.cargo/bin（cargo 常装在这里却不一定在 GUI 进程的 PATH 中），
// 再用当前 Node 解释器执行 CLI 的 JS 入口，参数原样透传。
// 不走 node_modules/.bin 的 .cmd 包装：Node 在 Windows 上启动 .cmd 必须经 shell，
// 路径含空格时转义容易出错。
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const tauriDir = path.resolve(scriptDir, "..");
const cliEntry = path.join(tauriDir, "node_modules", "@tauri-apps", "cli", "tauri.js");

if (!existsSync(cliEntry)) {
  console.error(`❌ 未找到 Tauri CLI: ${cliEntry}`);
  console.error("   请先在 tauri/ 目录执行 npm install");
  process.exit(1);
}

// Windows 环境变量名大小写不敏感，先按不区分大小写找到已有的 PATH 键再覆盖，
// 避免同时存在 Path / PATH 两个键导致取值不确定。
const env = { ...process.env };
const pathKey = Object.keys(env).find((key) => key.toUpperCase() === "PATH") ?? "PATH";
const cargoBin = path.join(homedir(), ".cargo", "bin");
const currentPath = env[pathKey] ?? "";
env[pathKey] = [cargoBin, currentPath].filter(Boolean).join(path.delimiter);

const child = spawn(process.execPath, [cliEntry, ...process.argv.slice(2)], {
  cwd: tauriDir,
  env,
  stdio: "inherit",
});

child.on("error", (error) => {
  console.error(`❌ 启动 Tauri CLI 失败: ${error.message}`);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 1);
});
