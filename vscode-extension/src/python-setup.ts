/**
 * Managed Python environment for marketplace installs.
 *
 * Creates a dedicated venv in VS Code's global storage and pip-installs
 * the CodeIndexer Python package from the bundled source inside the extension.
 * Users never need to pre-install anything.
 */

import { ChildProcess, spawn } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';

const isWin = process.platform === 'win32';
const venvBin = isWin ? 'Scripts' : 'bin';
const pyExe = isWin ? 'python.exe' : 'python';
const pipExe = isWin ? 'pip.exe' : 'pip';

export function getManagedVenvPath(globalStoragePath: string): string {
    return path.join(globalStoragePath, '.codeindexer-venv');
}

export function getManagedPythonPath(globalStoragePath: string): string {
    return path.join(getManagedVenvPath(globalStoragePath), venvBin, pyExe);
}

export function isManagedVenvReady(globalStoragePath: string): boolean {
    return fs.existsSync(getManagedPythonPath(globalStoragePath));
}

/**
 * Find a system Python 3.10+ suitable for creating a venv.
 */
export async function findSystemPython(): Promise<string | null> {
    const candidates = [
        'python3.13', 'python3.12', 'python3.11', 'python3.10',
        'python3', 'python',
    ];
    for (const py of candidates) {
        if (await canRun(py, ['--version'])) return py;
    }
    return null;
}

/**
 * Create a managed venv and install the bundled CodeIndexer package.
 * Streams all pip output to onLog so the user sees live progress.
 */
export async function setupManagedVenv(
    globalStoragePath: string,
    extensionPath: string,
    onLog: (msg: string) => void
): Promise<boolean> {
    const venvPath = getManagedVenvPath(globalStoragePath);
    const pythonSrc = path.join(extensionPath, 'python');

    if (!fs.existsSync(pythonSrc)) {
        onLog('[Setup] Bundled Python source not found. Please reinstall the extension.');
        return false;
    }

    const sysPython = await findSystemPython();
    if (!sysPython) {
        onLog('[Setup] No Python 3.10+ found on your system.');
        onLog('[Setup] Install Python from https://python.org then try again.');
        return false;
    }
    onLog(`[Setup] Using system Python: ${sysPython}`);
    onLog(`[Setup] Creating virtual environment at: ${venvPath}`);

    if (fs.existsSync(venvPath)) {
        fs.rmSync(venvPath, { recursive: true, force: true });
    }

    const venvOk = await runProcess(sysPython, ['-m', 'venv', venvPath], onLog);
    if (!venvOk) {
        onLog('[Setup] Failed to create virtual environment.');
        return false;
    }

    const pip = path.join(getManagedVenvPath(globalStoragePath), venvBin, pipExe);
    onLog('[Setup] Installing CodeIndexer and dependencies…');
    onLog('[Setup] This may take 5–10 minutes on first install (downloading ML models etc.)');

    const installOk = await runProcess(
        pip,
        ['install', '-e', pythonSrc, '--no-cache-dir'],
        onLog
    );

    if (installOk) {
        onLog('[Setup] ✓ CodeIndexer installed successfully!');
    } else {
        onLog('[Setup] Installation failed — check the output above for details.');
    }
    return installOk;
}

function canRun(cmd: string, args: string[]): Promise<boolean> {
    return new Promise((resolve) => {
        const p = spawn(cmd, args, { stdio: 'ignore' });
        p.on('exit', (c) => resolve(c === 0));
        p.on('error', () => resolve(false));
    });
}

function runProcess(
    cmd: string,
    args: string[],
    onLog: (msg: string) => void
): Promise<boolean> {
    return new Promise((resolve) => {
        let proc: ChildProcess;
        try {
            proc = spawn(cmd, args, {
                stdio: ['ignore', 'pipe', 'pipe'],
            });
        } catch (err: any) {
            onLog(`[Setup] Error: ${err.message}`);
            resolve(false);
            return;
        }

        proc.stdout?.on('data', (d: Buffer) => onLog(`[Setup] ${d.toString().trimEnd()}`));
        proc.stderr?.on('data', (d: Buffer) => onLog(`[Setup] ${d.toString().trimEnd()}`));
        proc.on('exit', (code) => resolve(code === 0));
        proc.on('error', (err) => {
            onLog(`[Setup] Error: ${err.message}`);
            resolve(false);
        });
    });
}
