/**
 * Copies the CodeIndexer Python source into vscode-extension/python/
 * so it gets bundled into the VSIX and is available for pip install at runtime.
 */

const fs = require('fs');
const path = require('path');

const projectRoot = path.resolve(__dirname, '..', '..');
const srcDir = path.join(projectRoot, 'src', 'code_indexer');
const pyprojectSrc = path.join(projectRoot, 'pyproject.toml');

const outDir = path.join(__dirname, '..', 'python');
const destSrc = path.join(outDir, 'src', 'code_indexer');
const pyprojectDest = path.join(outDir, 'pyproject.toml');

function copyDir(src, dest) {
    fs.mkdirSync(dest, { recursive: true });
    for (const item of fs.readdirSync(src)) {
        const s = path.join(src, item);
        const d = path.join(dest, item);
        if (fs.statSync(s).isDirectory()) {
            copyDir(s, d);
        } else {
            fs.copyFileSync(s, d);
        }
    }
}

if (fs.existsSync(outDir)) {
    fs.rmSync(outDir, { recursive: true, force: true });
}

copyDir(srcDir, destSrc);
fs.copyFileSync(pyprojectSrc, pyprojectDest);

const pyFiles = fs.readdirSync(destSrc, { recursive: true })
    .filter(f => f.toString().endsWith('.py')).length;

console.log(`[bundle-python] Bundled ${pyFiles} .py files → python/`);
