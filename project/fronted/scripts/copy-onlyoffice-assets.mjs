/**
 * 将 pi-view-port 自带的 OnlyOffice WASM 资源复制到 public，供 Next 静态托管。
 * 约 150MB，首次 npm install 后执行一次即可。
 */
import { cpSync, existsSync, mkdirSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = join(root, 'node_modules', 'pi-view-port', 'public', 'packages', 'onlyoffice');
const dest = join(root, 'public', 'packages', 'onlyoffice');

if (!existsSync(src)) {
  console.warn('[copy-onlyoffice] 未找到 pi-view-port 资源，请先执行: npm install pi-view-port');
  process.exit(0);
}

const force = process.env.FORCE_ONLYOFFICE_COPY === '1';
if (existsSync(dest) && !force) {
  console.log('[copy-onlyoffice] 已存在，跳过（FORCE_ONLYOFFICE_COPY=1 可强制覆盖）');
  process.exit(0);
}

mkdirSync(join(root, 'public', 'packages'), { recursive: true });
console.log('[copy-onlyoffice] 正在复制（约 150MB）…');
cpSync(src, dest, { recursive: true, force: true });
console.log('[copy-onlyoffice] 完成 ->', dest);
