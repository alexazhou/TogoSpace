import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const outputDir = path.resolve(__dirname, '../public/avatars');
const sourceDir = path.resolve(__dirname, '../../avatars_batch');

function writeDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function collectAvatarFiles(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  const files = [];

  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);

    if (entry.isDirectory()) {
      files.push(...collectAvatarFiles(fullPath));
      continue;
    }

    if (!entry.isFile()) {
      continue;
    }

    if (path.extname(entry.name).toLowerCase() !== '.png') {
      continue;
    }

    if (entry.name.startsWith('avatar_strip_')) {
      continue;
    }

    files.push(fullPath);
  }

  return files.sort((left, right) =>
    path.relative(sourceDir, left).localeCompare(path.relative(sourceDir, right)),
  );
}

writeDir(outputDir);

for (const entry of fs.readdirSync(outputDir, { withFileTypes: true })) {
  if (!entry.isFile()) {
    continue;
  }

  const ext = path.extname(entry.name).toLowerCase();
  if (ext === '.png' || ext === '.svg') {
    fs.rmSync(path.join(outputDir, entry.name));
  }
}

const avatarFiles = collectAvatarFiles(sourceDir);

if (avatarFiles.length === 0) {
  throw new Error(`No avatar PNGs found in ${sourceDir}`);
}

for (let index = 0; index < avatarFiles.length; index += 1) {
  const outputName = `${String(index + 1).padStart(3, '0')}.png`;
  fs.copyFileSync(avatarFiles[index], path.join(outputDir, outputName));
}

console.log(`copied ${avatarFiles.length} avatars into ${outputDir}`);
