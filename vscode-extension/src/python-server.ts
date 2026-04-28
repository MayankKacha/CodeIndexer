/**
 * Python CodeIndexer API server lifecycle manager.
 *
 * Spawns the FastAPI server as a child process, monitors health,
 * and handles graceful shutdown.
 */

import { ChildProcess, spawn, spawnSync } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import * as http from 'http';
import * as net from 'net';

export interface PythonServerOptions {
    pythonPath: string;
    port: number;
    cwd: string;
    onLog?: (message: string) => void;
    onExit?: (code: number | null) => void;
}

let serverProcess: ChildProcess | null = null;
let isShuttingDown = false;
let killTimer: NodeJS.Timeout | null = null;
let cachedRoot: string | null | undefined = undefined;

const isWin = process.platform === 'win32';
const venvBinDir = isWin ? 'Scripts' : 'bin';
const pyExe = isWin ? 'python.exe' : 'python';
const pyExe3 = isWin ? 'python.exe' : 'python3';

/**
 * Auto-detect the Python executable path (cross-platform).
 */
export function detectPythonPath(workspaceRoot: string): string {
    const venvPaths = [
        path.join(workspaceRoot, '.venv', venvBinDir, pyExe),
        path.join(workspaceRoot, '.venv', venvBinDir, pyExe3),
        path.join(workspaceRoot, 'venv', venvBinDir, pyExe),
        path.join(workspaceRoot, 'venv', venvBinDir, pyExe3),
    ];

    for (const p of venvPaths) {
        if (fs.existsSync(p)) {
            return p;
        }
    }

    const codeIndexerRoot = findCodeIndexerRoot();
    if (codeIndexerRoot) {
        const ciVenv = path.join(codeIndexerRoot, '.venv', venvBinDir, pyExe);
        if (fs.existsSync(ciVenv)) {
            return ciVenv;
        }
    }

    const fallbacks = isWin ? ['python', 'python3', 'py'] : ['python3', 'python'];
    for (const cmd of fallbacks) {
        if (canRunSync(cmd)) return cmd;
    }
    return fallbacks[0];
}

function canRunSync(cmd: string): boolean {
    try {
        const r = spawnSync(cmd, ['--version'], { stdio: 'ignore' });
        return r.status === 0;
    } catch {
        return false;
    }
}

/**
 * Find the CodeIndexer project root by looking for pyproject.toml. Cached.
 */
export function findCodeIndexerRoot(): string | null {
    if (cachedRoot !== undefined) return cachedRoot;

    const extensionDir = path.resolve(__dirname, '..');
    const parentDir = path.resolve(extensionDir, '..');

    const pyproject = path.join(parentDir, 'pyproject.toml');
    if (fs.existsSync(pyproject)) {
        try {
            const content = fs.readFileSync(pyproject, 'utf-8');
            if (content.includes('code-indexer') || content.includes('code_indexer')) {
                cachedRoot = parentDir;
                return cachedRoot;
            }
        } catch {
            // ignore read errors
        }
    }

    cachedRoot = null;
    return null;
}

/**
 * Quickly check that the chosen Python can import the modules the API server
 * needs. Fails in a few hundred ms instead of letting uvicorn time out after 30s.
 */
export function validatePython(
    pythonPath: string,
    extraPythonPath?: string
): Promise<{ ok: boolean; error: string }> {
    return new Promise((resolve) => {
        const env = { ...process.env };
        if (extraPythonPath) {
            env.PYTHONPATH = extraPythonPath + (env.PYTHONPATH ? `${path.delimiter}${env.PYTHONPATH}` : '');
        }

        let proc: ChildProcess;
        try {
            proc = spawn(pythonPath, ['-c', 'import code_indexer, uvicorn'], {
                stdio: ['ignore', 'ignore', 'pipe'],
                env,
            });
        } catch (err: any) {
            resolve({ ok: false, error: err.message });
            return;
        }

        let stderr = '';
        proc.stderr?.on('data', (d: Buffer) => { stderr += d.toString(); });

        const timer = setTimeout(() => {
            if (!proc.killed) proc.kill();
            resolve({ ok: false, error: 'Python validation timed out after 5s' });
        }, 5000);

        proc.on('exit', (code) => {
            clearTimeout(timer);
            resolve({ ok: code === 0, error: stderr.trim() || `exit code ${code}` });
        });
        proc.on('error', (err) => {
            clearTimeout(timer);
            resolve({ ok: false, error: err.message });
        });
    });
}

/**
 * Find a free TCP port, preferring the requested one.
 */
export function findFreePort(preferred: number): Promise<number> {
    return new Promise((resolve) => {
        const tryPreferred = net.createServer();
        tryPreferred.once('error', () => {
            const ephemeral = net.createServer();
            ephemeral.listen(0, '127.0.0.1', () => {
                const port = (ephemeral.address() as net.AddressInfo).port;
                ephemeral.close(() => resolve(port));
            });
        });
        tryPreferred.once('listening', () => {
            tryPreferred.close(() => resolve(preferred));
        });
        tryPreferred.listen(preferred, '127.0.0.1');
    });
}

/**
 * Start the Python CodeIndexer API server.
 */
export function startPythonServer(options: PythonServerOptions): ChildProcess {
    const { pythonPath, port, cwd, onLog, onExit } = options;
    isShuttingDown = false;

    const log = onLog || console.log;

    log(`[CodeIndexer] Starting Python API server on port ${port}...`);
    log(`[CodeIndexer] Python: ${pythonPath}`);

    const codeIndexerRoot = findCodeIndexerRoot();
    const env = { ...process.env };

    if (codeIndexerRoot) {
        const srcPath = path.join(codeIndexerRoot, 'src');
        env.PYTHONPATH = srcPath + (env.PYTHONPATH ? `${path.delimiter}${env.PYTHONPATH}` : '');
    }

    const serverCwd = codeIndexerRoot || cwd;
    log(`[CodeIndexer] CWD: ${serverCwd}`);

    serverProcess = spawn(
        pythonPath,
        [
            '-m', 'uvicorn',
            'code_indexer.api.server:app',
            '--host', '127.0.0.1',
            '--port', String(port),
            '--log-level', 'info',
        ],
        {
            cwd: serverCwd,
            env,
            stdio: ['ignore', 'pipe', 'pipe'],
        }
    );

    serverProcess.stdout?.on('data', (data: Buffer) => {
        log(`[CodeIndexer API] ${data.toString().trim()}`);
    });

    serverProcess.stderr?.on('data', (data: Buffer) => {
        log(`[CodeIndexer API] ${data.toString().trim()}`);
    });

    serverProcess.on('exit', (code) => {
        if (killTimer) {
            clearTimeout(killTimer);
            killTimer = null;
        }
        if (!isShuttingDown) {
            log(`[CodeIndexer] Python server exited with code ${code}`);
            if (onExit) {
                onExit(code);
            }
        }
        serverProcess = null;
    });

    serverProcess.on('error', (err) => {
        log(`[CodeIndexer] Failed to start Python server: ${err.message}`);
    });

    return serverProcess;
}

/**
 * Stop the Python server gracefully.
 */
export function stopPythonServer(): void {
    isShuttingDown = true;
    if (serverProcess) {
        serverProcess.kill('SIGTERM');
        if (killTimer) clearTimeout(killTimer);
        killTimer = setTimeout(() => {
            if (serverProcess && !serverProcess.killed) {
                serverProcess.kill('SIGKILL');
            }
            killTimer = null;
        }, 5000);
    }
}

/**
 * Check if the Python server is running and healthy.
 */
export function checkServerHealth(port: number): Promise<boolean> {
    return new Promise((resolve) => {
        const req = http.get(`http://127.0.0.1:${port}/api/health`, (res) => {
            resolve(res.statusCode === 200);
        });
        req.on('error', () => resolve(false));
        req.setTimeout(2000, () => {
            req.destroy();
            resolve(false);
        });
    });
}

/**
 * Wait for the Python server to become healthy, with exponential backoff.
 */
export async function waitForServer(
    port: number,
    maxWaitMs: number = 30000,
    onLog?: (msg: string) => void
): Promise<boolean> {
    const start = Date.now();
    const log = onLog || console.log;
    let delay = 100;

    while (Date.now() - start < maxWaitMs) {
        if (await checkServerHealth(port)) {
            log(`[CodeIndexer] Python API server is ready on port ${port}`);
            return true;
        }
        await new Promise((r) => setTimeout(r, delay));
        delay = Math.min(delay * 2, 1000);
    }

    log(`[CodeIndexer] Timeout waiting for Python server after ${maxWaitMs}ms`);
    return false;
}

/**
 * Get the server process (for status checks).
 */
export function getServerProcess(): ChildProcess | null {
    return serverProcess;
}
