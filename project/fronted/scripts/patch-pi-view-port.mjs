/**
 * pi-view-port 0.1.x 在 readOnly 时未设置 OnlyOffice mode:'view'，
 * 导致编辑器以编辑模式加载后再禁用权限，工具栏全部置灰不可用。
 */
import { readdirSync, readFileSync, writeFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const distDir = join(root, 'node_modules', 'pi-view-port', 'dist');

if (!existsSync(distDir)) {
  console.log('[patch-pi-view-port] pi-view-port 未安装，跳过');
  process.exit(0);
}

const MODE_OLD = "// mode: readOnly ? 'view' : 'edit', // 根据 readOnly 参数设置模式";
const MODE_NEW = "mode: readOnly ? 'view' : 'edit',";
const EDIT_OLD = '// edit: !readOnly, // 根据 readOnly 参数设置编辑权限';
const EDIT_NEW = 'edit: !readOnly,';

let patched = 0;
for (const file of readdirSync(distDir)) {
  if (!file.startsWith('index-') || !file.endsWith('.mjs')) continue;
  const path = join(distDir, file);
  let content = readFileSync(path, 'utf8');
  if (!content.includes(MODE_OLD) && !content.includes(MODE_NEW)) continue;
  if (content.includes(MODE_OLD)) {
    content = content.replace(MODE_OLD, MODE_NEW);
  }
  if (content.includes(EDIT_OLD)) {
    content = content.replace(EDIT_OLD, EDIT_NEW);
  }
  writeFileSync(path, content, 'utf8');
  patched += 1;
  console.log('[patch-pi-view-port] 已修复 OnlyOffice 只读预览 ->', file);
}

if (patched === 0) {
  console.warn('[patch-pi-view-port] 未找到可补丁文件（可能已打过补丁或版本变更）');
}
