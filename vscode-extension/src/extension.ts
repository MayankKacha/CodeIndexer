/**
 * CodeIndexer MCP — VS Code Extension
 *
 * On activation:
 *   1. Ensures a managed Python venv exists (creates + pip-installs the bundled
 *      CodeIndexer package on first launch — no manual install needed).
 *   2. Starts the Python FastAPI server.
 *   3. Auto-indexes the current workspace if it isn't indexed yet.
 *   4. Starts a file watcher that reindexes individual files on save/create
 *      and removes them from the index on delete. The Python side hash-checks
 *      so save events on unchanged content are no-ops.
 *
 * The MCP server definition provider exposes the search/graph/RAG tools to
 * VS Code's language model so the user can ask things like "where would I
 * add Redis?", "find every TODO in the codebase", or "what's the impact of
 * changing X" without ever leaving the chat surface.
 */

import * as vscode from 'vscode';
import * as path from 'path';
import * as http from 'http';
import {
    detectPythonPath,
    startPythonServer,
    stopPythonServer,
    waitForServer,
    checkServerHealth,
    findFreePort,
    findCodeIndexerRoot,
    validatePython,
} from './python-server';
import {
    getManagedPythonPath,
    isManagedVenvReady,
    setupManagedVenv,
} from './python-setup';

let outputChannel: vscode.OutputChannel;
let resolvedPort: number | null = null;
let extensionContext: vscode.ExtensionContext;
let fileWatcher: vscode.FileSystemWatcher | undefined;
const pendingReindex = new Map<string, NodeJS.Timeout>();
const REINDEX_DEBOUNCE_MS = 500;

function buildDefinition(
    context: vscode.ExtensionContext,
    port: number
): vscode.McpStdioServerDefinition {
    const serverPath = context.asAbsolutePath(path.join('dist', 'mcp-server.js'));
    return new vscode.McpStdioServerDefinition(
        'CodeIndexer',
        'node',
        [serverPath],
        { CODEINDEXER_API_PORT: String(port) },
        '0.2.0'
    );
}

async function showPythonSetupError(pythonPath: string, error: string): Promise<void> {
    const isModuleMissing = /No module named ['"]?code_indexer/i.test(error);
    const message = isModuleMissing
        ? `CodeIndexer: Python at "${pythonPath}" does not have code_indexer installed.`
        : `CodeIndexer cannot start: ${error.split('\n').slice(-2).join(' ')}`;

    const choice = await vscode.window.showErrorMessage(
        message,
        'Install Automatically',
        'Open Settings',
        'View Output'
    );

    if (choice === 'Install Automatically') {
        await runAutoSetup();
    } else if (choice === 'Open Settings') {
        await vscode.commands.executeCommand(
            'workbench.action.openSettings',
            'codeindexer.pythonPath'
        );
    } else if (choice === 'View Output') {
        outputChannel.show();
    }
}

async function runAutoSetup(): Promise<void> {
    outputChannel.show();
    outputChannel.appendLine('\n[Setup] Starting automatic CodeIndexer installation...');

    const globalStoragePath = extensionContext.globalStorageUri.fsPath;
    const extensionPath = extensionContext.extensionPath;

    const ok = await vscode.window.withProgress(
        {
            location: vscode.ProgressLocation.Notification,
            title: 'Installing CodeIndexer dependencies…',
            cancellable: false,
        },
        async (progress) => {
            progress.report({ message: 'Creating virtual environment…' });
            return await setupManagedVenv(
                globalStoragePath,
                extensionPath,
                (msg) => {
                    outputChannel.appendLine(msg);
                    if (msg.includes('Installing')) {
                        progress.report({ message: msg.replace('[Setup] ', '') });
                    }
                }
            );
        }
    );

    if (!ok) {
        vscode.window.showErrorMessage(
            'CodeIndexer installation failed. Check the CodeIndexer MCP output channel for details.',
            'View Output'
        ).then((c) => { if (c === 'View Output') outputChannel.show(); });
        return;
    }

    const managedPython = getManagedPythonPath(globalStoragePath);
    await vscode.workspace.getConfiguration('codeindexer').update(
        'pythonPath',
        managedPython,
        vscode.ConfigurationTarget.Global
    );
    outputChannel.appendLine(`[Setup] Saved Python path: ${managedPython}`);

    vscode.window.showInformationMessage('CodeIndexer installed. Starting the API server…');

    const config = vscode.workspace.getConfiguration('codeindexer');
    const apiPort = config.get<number>('apiPort', 8000);
    resolvedPort = null;
    const port = await ensurePythonServer(apiPort);
    if (port) {
        autoIndexAndWatch(port).catch((err) =>
            outputChannel.appendLine(`[Auto-index] Failed: ${err.message}`)
        );
    }
}

export async function activate(context: vscode.ExtensionContext) {
    extensionContext = context;
    outputChannel = vscode.window.createOutputChannel('CodeIndexer MCP');
    outputChannel.appendLine('CodeIndexer MCP extension activating...');

    const config = vscode.workspace.getConfiguration('codeindexer');
    const configuredPort = config.get<number>('apiPort', 8000);

    const globalStoragePath = context.globalStorageUri.fsPath;
    if (!config.get<string>('pythonPath', '') && isManagedVenvReady(globalStoragePath)) {
        const managedPython = getManagedPythonPath(globalStoragePath);
        outputChannel.appendLine(`[Setup] Using managed venv: ${managedPython}`);
        await config.update('pythonPath', managedPython, vscode.ConfigurationTarget.Global);
    }

    const didChangeEmitter = new vscode.EventEmitter<void>();

    const mcpProvider = vscode.lm.registerMcpServerDefinitionProvider(
        'codeindexer-mcp-provider',
        {
            onDidChangeMcpServerDefinitions: didChangeEmitter.event,
            provideMcpServerDefinitions: async () => [buildDefinition(context, resolvedPort ?? configuredPort)],
            resolveMcpServerDefinition: async () => {
                await ensurePythonServer(configuredPort);
                return buildDefinition(context, resolvedPort ?? configuredPort);
            },
        }
    );
    context.subscriptions.push(mcpProvider);

    context.subscriptions.push(
        vscode.commands.registerCommand('codeindexer.startServer', () => ensurePythonServer(configuredPort))
    );
    context.subscriptions.push(
        vscode.commands.registerCommand('codeindexer.stopServer', () => {
            stopPythonServer();
            resolvedPort = null;
            disposeFileWatcher();
            outputChannel.appendLine('[Server] Python API server stopped');
            vscode.window.showInformationMessage('CodeIndexer API server stopped.');
        })
    );

    // Boot the server eagerly; auto-index hooks in once it's healthy.
    if (config.get<boolean>('autoStartServer', true)) {
        ensurePythonServer(configuredPort)
            .then((port) => {
                if (port) {
                    autoIndexAndWatch(port).catch((err) =>
                        outputChannel.appendLine(`[Auto-index] Failed: ${err.message}`)
                    );
                }
            })
            .catch((err) => outputChannel.appendLine(`[Server] Auto-start failed: ${err.message}`));
    }

    outputChannel.appendLine('CodeIndexer MCP extension activated');
}

async function ensurePythonServer(configuredPort: number): Promise<number | null> {
    if (resolvedPort && (await checkServerHealth(resolvedPort))) {
        return resolvedPort;
    }

    if (await checkServerHealth(configuredPort)) {
        resolvedPort = configuredPort;
        outputChannel.appendLine(`[Server] Reusing existing API server on port ${configuredPort}`);
        return resolvedPort;
    }

    const port = await findFreePort(configuredPort);
    if (port !== configuredPort) {
        outputChannel.appendLine(`[Server] Port ${configuredPort} busy, using ${port}`);
    }

    const config = vscode.workspace.getConfiguration('codeindexer');
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || process.cwd();
    const configuredPython = config.get<string>('pythonPath', '');
    const pythonPath = configuredPython || detectPythonPath(workspaceRoot);

    outputChannel.appendLine(`[Server] Using Python: ${pythonPath}`);

    const ciRoot = findCodeIndexerRoot();
    const extraPyPath = ciRoot ? path.join(ciRoot, 'src') : undefined;
    const validation = await validatePython(pythonPath, extraPyPath);
    if (!validation.ok) {
        outputChannel.appendLine(`[Server] Python validation failed: ${validation.error}`);
        await showPythonSetupError(pythonPath, validation.error);
        return null;
    }

    startPythonServer({
        pythonPath,
        port,
        cwd: workspaceRoot,
        onLog: (msg) => outputChannel.appendLine(msg),
        onExit: (code) => {
            resolvedPort = null;
            if (code !== 0 && code !== null) {
                vscode.window.showWarningMessage(
                    `CodeIndexer API server exited with code ${code}. Check the output panel for details.`
                );
            }
        },
    });

    const ready = await waitForServer(port, 30000, (msg) => outputChannel.appendLine(msg));
    if (ready) {
        resolvedPort = port;
        return port;
    }

    vscode.window.showWarningMessage(
        'CodeIndexer API server did not start in time. You can try "CodeIndexer: Start Server" manually.'
    );
    return null;
}

// ── Auto-index + file watcher ─────────────────────────────────────────

async function autoIndexAndWatch(port: number): Promise<void> {
    const folder = vscode.workspace.workspaceFolders?.[0];
    if (!folder) {
        outputChannel.appendLine('[Auto-index] No workspace folder open — skipping');
        return;
    }
    const wsPath = folder.uri.fsPath;
    const repoName = path.basename(wsPath);

    const indexed = await isWorkspaceIndexed(port, repoName);
    if (!indexed) {
        outputChannel.appendLine(`[Auto-index] Indexing workspace "${repoName}" for the first time…`);
        vscode.window.showInformationMessage(`CodeIndexer: indexing ${repoName} in the background…`);
        try {
            await runFullIndex(port, wsPath, repoName);
            await extensionContext.workspaceState.update('codeindexer.indexed', true);
            vscode.window.showInformationMessage(`CodeIndexer: ${repoName} ready.`);
        } catch (err: any) {
            outputChannel.appendLine(`[Auto-index] Initial index failed: ${err.message}`);
            vscode.window.showErrorMessage(`CodeIndexer indexing failed: ${err.message}`);
            return;
        }
    } else {
        outputChannel.appendLine(`[Auto-index] "${repoName}" already indexed — incremental updates only`);
    }

    startFileWatcher(folder, repoName, wsPath, port);
}

async function isWorkspaceIndexed(port: number, repoName: string): Promise<boolean> {
    if (extensionContext.workspaceState.get<boolean>('codeindexer.indexed')) {
        return true;
    }
    try {
        const repos = await httpGetJson<Array<{ repo_name?: string; name?: string }>>(
            port, '/api/repositories'
        );
        const list = Array.isArray(repos) ? repos : (repos as any)?.repositories ?? [];
        const found = list.some((r: any) => (r.repo_name ?? r.name) === repoName);
        if (found) {
            await extensionContext.workspaceState.update('codeindexer.indexed', true);
        }
        return found;
    } catch (err: any) {
        outputChannel.appendLine(`[Auto-index] /api/repositories check failed: ${err.message}`);
        return false;
    }
}

function runFullIndex(port: number, wsPath: string, repoName: string): Promise<void> {
    return new Promise<void>((resolve, reject) => {
        const postData = JSON.stringify({
            path: wsPath,
            repo_name: repoName,
            generate_descriptions: false,
        });
        const req = http.request(
            {
                hostname: '127.0.0.1',
                port,
                path: '/api/index',
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Content-Length': Buffer.byteLength(postData),
                },
            },
            (res) => {
                let buf = '';
                res.on('data', (chunk: Buffer) => {
                    buf += chunk.toString();
                    const events = buf.split('\n\n');
                    buf = events.pop() ?? '';
                    for (const evt of events) {
                        for (const line of evt.split('\n')) {
                            if (line.startsWith('data: ')) {
                                try {
                                    const event = JSON.parse(line.substring(6));
                                    outputChannel.appendLine(
                                        `[Index] ${event.step}: ${event.message}`
                                    );
                                } catch {
                                    /* ignore parse errors */
                                }
                            }
                        }
                    }
                });
                res.on('end', resolve);
            }
        );
        req.on('error', reject);
        req.write(postData);
        req.end();
    });
}

function startFileWatcher(
    folder: vscode.WorkspaceFolder,
    repoName: string,
    wsPath: string,
    port: number
): void {
    disposeFileWatcher();

    const pattern = new vscode.RelativePattern(folder, '**/*');
    fileWatcher = vscode.workspace.createFileSystemWatcher(pattern);

    const queueReindex = (uri: vscode.Uri) => {
        const filePath = uri.fsPath;
        if (shouldIgnore(filePath, wsPath)) return;
        const existing = pendingReindex.get(filePath);
        if (existing) clearTimeout(existing);
        pendingReindex.set(
            filePath,
            setTimeout(() => {
                pendingReindex.delete(filePath);
                reindexOneFile(port, repoName, filePath, wsPath).catch((err) =>
                    outputChannel.appendLine(`[Watch] Reindex failed for ${filePath}: ${err.message}`)
                );
            }, REINDEX_DEBOUNCE_MS)
        );
    };

    fileWatcher.onDidChange(queueReindex);
    fileWatcher.onDidCreate(queueReindex);
    fileWatcher.onDidDelete((uri) => {
        const filePath = uri.fsPath;
        if (shouldIgnore(filePath, wsPath)) return;
        const pending = pendingReindex.get(filePath);
        if (pending) {
            clearTimeout(pending);
            pendingReindex.delete(filePath);
        }
        deleteOneFile(port, repoName, filePath).catch((err) =>
            outputChannel.appendLine(`[Watch] Remove failed for ${filePath}: ${err.message}`)
        );
    });

    extensionContext.subscriptions.push(fileWatcher);
    outputChannel.appendLine(`[Watch] File watcher active for ${wsPath}`);
}

function disposeFileWatcher(): void {
    for (const t of pendingReindex.values()) clearTimeout(t);
    pendingReindex.clear();
    if (fileWatcher) {
        fileWatcher.dispose();
        fileWatcher = undefined;
    }
}

const IGNORED_DIRS = [
    '/node_modules/', '/.git/', '/.venv/', '/venv/', '/__pycache__/',
    '/dist/', '/build/', '/.codeindexer_cache/', '/.vscode/', '/.idea/',
    '/.next/', '/.nuxt/', '/coverage/', '/.pytest_cache/',
];

function shouldIgnore(filePath: string, wsRoot: string): boolean {
    const norm = filePath.replace(/\\/g, '/') + '/';
    if (IGNORED_DIRS.some((d) => norm.includes(d))) return true;
    // Skip non-source-y stuff cheaply; the Python side will also no-op
    // unsupported extensions, so this is just bandwidth savings.
    return /\.(lock|log|tmp|swp|map|min\.js|min\.css)$/i.test(filePath);
}

async function reindexOneFile(
    port: number,
    repoName: string,
    filePath: string,
    wsPath: string
): Promise<void> {
    const result = await httpPostJson<any>(port, '/api/index/file', {
        repo_name: repoName,
        file_path: filePath,
        repo_root: wsPath,
    });
    if (result?.status && result.status !== 'unchanged') {
        outputChannel.appendLine(
            `[Watch] ${result.status}: ${result.file ?? filePath}` +
            (result.elements != null ? ` (${result.elements} elements)` : '')
        );
    }
}

async function deleteOneFile(port: number, repoName: string, filePath: string): Promise<void> {
    const url = `/api/index/file?repo_name=${encodeURIComponent(repoName)}&file_path=${encodeURIComponent(filePath)}`;
    await httpRequest(port, url, 'DELETE');
    outputChannel.appendLine(`[Watch] removed ${filePath}`);
}

// ── HTTP helpers ──────────────────────────────────────────────────────

function httpRequest(
    port: number,
    pathAndQuery: string,
    method: string,
    body?: string
): Promise<{ status: number; body: string }> {
    return new Promise((resolve, reject) => {
        const headers: Record<string, string | number> = {};
        if (body) {
            headers['Content-Type'] = 'application/json';
            headers['Content-Length'] = Buffer.byteLength(body);
        }
        const req = http.request(
            { hostname: '127.0.0.1', port, path: pathAndQuery, method, headers },
            (res) => {
                let data = '';
                res.on('data', (chunk: Buffer) => { data += chunk.toString(); });
                res.on('end', () => resolve({ status: res.statusCode ?? 0, body: data }));
            }
        );
        req.on('error', reject);
        if (body) req.write(body);
        req.end();
    });
}

async function httpGetJson<T>(port: number, pathAndQuery: string): Promise<T> {
    const { status, body } = await httpRequest(port, pathAndQuery, 'GET');
    if (status >= 400) throw new Error(`GET ${pathAndQuery} → ${status}: ${body}`);
    return JSON.parse(body) as T;
}

async function httpPostJson<T>(port: number, pathAndQuery: string, payload: unknown): Promise<T> {
    const body = JSON.stringify(payload);
    const { status, body: respBody } = await httpRequest(port, pathAndQuery, 'POST', body);
    if (status >= 400) throw new Error(`POST ${pathAndQuery} → ${status}: ${respBody}`);
    return JSON.parse(respBody) as T;
}

export function deactivate() {
    outputChannel?.appendLine('CodeIndexer MCP extension deactivating...');
    disposeFileWatcher();
    stopPythonServer();
    outputChannel?.dispose();
}
