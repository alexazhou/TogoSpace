#!/usr/bin/env node
// `tauri.conf.json` 的 beforeBuildCommand 入口：跨平台定位仓库 .venv 后调用 build_backend.py。
//
// 原命令是 `.venv/bin/python3 tauri/scripts/build_backend.py`，只有 POSIX 布局存在；
// Windows 的虚拟环境解释器在 `.venv\Scripts\python.exe`，直接照抄会在构建阶段报
// 「找不到文件」。这里按平台选择解释器，缺失时回退到 PATH 中的 python。
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..", "..");

const venvPython =
  process.platform === "win32"
    ? path.join(repoRoot, ".venv", "Scripts", "python.exe")
    : path.join(repoRoot, ".venv", "bin", "python3");

const python = existsSync(venvPython)
  ? venvPython
  : process.platform === "win32"
    ? "python"
    : "python3";

if (!existsSync(venvPython)) {
  console.warn(`⚠️  未找到仓库虚拟环境 ${venvPython}，回退到 PATH 中的 ${python}`);
}

const args = [path.join(repoRoot, "tauri", "scripts", "build_backend.py"), ...process.argv.slice(2)];
const result = spawnSync(python, args, { cwd: repoRoot, stdio: "inherit" });

if (result.error) {
  console.error(`❌ 启动后端构建失败: ${result.error.message}`);
  process.exit(1);
}
if (result.signal) {
  process.kill(process.pid, result.signal);
} else {
  process.exit(result.status ?? 1);
}
