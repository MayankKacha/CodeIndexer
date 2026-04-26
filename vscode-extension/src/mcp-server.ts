#!/usr/bin/env node

/**
 * CodeIndexer MCP Server — 10 Granular Tools
 *
 * Design optimized for LLM context windows:
 * - "Discovery" tools return compact metadata (names, locations, signatures) — low tokens
 * - "Content" tools return actual source code — only when LLM explicitly needs it
 * - "Relationship" tools return graph edges — callers, callees, impact, chains
 *
 * This separation means an LLM can:
 * 1. Discover what exists (cheap)
 * 2. Understand relationships (cheap)
 * 3. Fetch code only for the specific elements it needs (targeted)
 *
 * Tools:
 *   1. codebase_overview  — stats, languages, repos, semantic confidence
 *   2. search_code        — semantic search → compact results (NO code)
 *   3. find_symbol        — find by name → compact results (NO code)
 *   4. get_code           — get FULL source code of a specific function/class
 *   5. get_callers        — who calls this function?
 *   6. get_callees        — what does this function call?
 *   7. get_impact         — what breaks if I change this?
 *   8. get_call_chain     — path between two functions
 *   9. get_file_structure — all functions/classes in a file
 *  10. find_dead_code     — find unused functions
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import * as http from 'http';

// ── Configuration ─────────────────────────────────────────────────────

const API_PORT = parseInt(process.env.CODEINDEXER_API_PORT || '8000', 10);
const API_BASE = `http://127.0.0.1:${API_PORT}`;

// Reuse TCP sockets across MCP tool calls — many calls per LLM session.
const httpAgent = new http.Agent({ keepAlive: true, maxSockets: 4 });

// ── HTTP Helper ───────────────────────────────────────────────────────

function apiRequest(
    method: string,
    path: string,
    body?: Record<string, unknown>
): Promise<{ status: number; data: any }> {
    return new Promise((resolve, reject) => {
        const url = new URL(path, API_BASE);
        const options: http.RequestOptions = {
            hostname: url.hostname,
            port: url.port,
            path: url.pathname + url.search,
            method,
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            timeout: 60000,
            agent: httpAgent,
        };

        const req = http.request(options, (res) => {
            let data = '';
            res.on('data', (chunk: Buffer) => { data += chunk.toString(); });
            res.on('end', () => {
                try {
                    resolve({ status: res.statusCode || 500, data: JSON.parse(data) });
                } catch {
                    resolve({ status: res.statusCode || 500, data: { raw: data } });
                }
            });
        });

        req.on('error', (err) => reject(new Error(
            `CodeIndexer API unreachable: ${err.message}. Is the server running on port ${API_PORT}?`
        )));
        req.on('timeout', () => { req.destroy(); reject(new Error('API request timed out')); });

        if (body) req.write(JSON.stringify(body));
        req.end();
    });
}

function text(content: string) {
    return { content: [{ type: 'text' as const, text: content }] };
}

function error(message: string) {
    return { content: [{ type: 'text' as const, text: message }], isError: true as const };
}

// ── MCP Server ────────────────────────────────────────────────────────

const server = new McpServer({
    name: 'codeindexer',
    version: '0.2.0',
});

// ═══════════════════════════════════════════════════════════════════════
// TOOL 1: Codebase Overview
// ═══════════════════════════════════════════════════════════════════════

server.registerTool(
    'codebase_overview',
    {
        description: `Get a high-level overview of all indexed codebases.

Returns for each repository:
- Total functions, methods, classes, files, lines of code
- Languages used
- Semantic confidence score (0-1): what % of code has docstrings/comments
  - Score 0.8 = 80% documented → semantic search is reliable
  - Score 0.2 = 20% documented → prefer graph-based tools

CALL THIS FIRST to understand what's available before using other tools.`,
        inputSchema: {
            repo_name: z.string().optional().describe('Filter by repository name. Omit to see all repos.'),
        },
    },
    async ({ repo_name }) => {
        try {
            const qp = repo_name ? `?repo_name=${encodeURIComponent(repo_name)}` : '';
            const res = await apiRequest('GET', `/api/mcp/overview${qp}`);
            if (res.status !== 200) return error(`Failed: ${JSON.stringify(res.data)}`);

            const repos = res.data.repositories || [];
            if (repos.length === 0) return text('No indexed repositories found. Use index_workspace first.');

            const lines = repos.map((r: any) =>
                `## ${r.repo_name}
- **Elements:** ${r.total_elements} (${r.functions} functions, ${r.methods} methods, ${r.classes} classes)
- **Files:** ${r.total_files} | **Lines:** ${r.total_lines}
- **Languages:** ${Object.entries(r.languages || {}).map(([l, c]) => `${l}(${c})`).join(', ')}
- **Semantic confidence:** ${r.semantic_confidence} (${r.elements_with_docs} of ${r.total_elements} have docs)
- **Indexed:** ${r.indexed_at}`
            ).join('\n\n');

            return text(lines);
        } catch (err: any) { return error(err.message); }
    }
);

// ═══════════════════════════════════════════════════════════════════════
// TOOL 2: Search Code (semantic — compact, NO source code)
// ═══════════════════════════════════════════════════════════════════════

server.registerTool(
    'search_code',
    {
        description: `Semantic search: find code by natural language description.

Returns COMPACT results: name, type, file, line numbers, signature, description.
Does NOT include full source code — use get_code to fetch specific implementations.

Example queries:
- "authentication logic"
- "database connection handling"  
- "payment processing"
- "error retry with backoff"`,
        inputSchema: {
            query: z.string().describe('Natural language description of what you are looking for'),
            top_k: z.number().optional().default(10).describe('Max results (default 10)'),
            repo_name: z.string().optional().describe('Filter by repository name'),
        },
    },
    async ({ query, top_k, repo_name }) => {
        try {
            const res = await apiRequest('POST', '/api/mcp/search', {
                query, top_k: top_k || 10, repo_name: repo_name || '', use_reranker: true,
            });
            if (res.status !== 200) return error(`Search failed: ${JSON.stringify(res.data)}`);

            const results = res.data.results || [];
            if (results.length === 0) return text(`No results for "${query}".`);

            const lines = results.map((r: any, i: number) =>
                `${i + 1}. **${r.qualified_name || r.name}** (${r.element_type}) — ${r.file_path}:${r.start_line}-${r.end_line}${r.signature ? `\n   Signature: \`${r.signature}\`` : ''}${r.description ? `\n   ${r.description}` : ''}`
            ).join('\n');

            return text(`## Search: "${query}" — ${results.length} results\n\n${lines}\n\n_Use get_code("name") to see the implementation of any result._`);
        } catch (err: any) { return error(err.message); }
    }
);

// ═══════════════════════════════════════════════════════════════════════
// TOOL 3: Find Symbol (by name — compact, NO source code)
// ═══════════════════════════════════════════════════════════════════════

server.registerTool(
    'find_symbol',
    {
        description: `Find functions, methods, or classes by exact or partial name.

Returns compact metadata: name, type, file, line numbers, signature, complexity.
Does NOT include source code — use get_code to fetch the implementation.

Supports partial matching: "encode" finds "encode", "encode_batch", "CodeEncoder", etc.`,
        inputSchema: {
            name: z.string().describe('Exact or partial function/method/class name'),
            repo_name: z.string().optional().describe('Filter by repository name'),
        },
    },
    async ({ name, repo_name }) => {
        try {
            const res = await apiRequest('POST', '/api/mcp/find-symbol', {
                name, repo_name: repo_name || '',
            });
            if (res.status !== 200) return error(`Find failed: ${JSON.stringify(res.data)}`);

            const results = res.data.results || [];
            if (results.length === 0) return text(`No symbols matching "${name}" found.`);

            const lines = results.map((r: any, i: number) =>
                `${i + 1}. **${r.qualified_name || r.name}** (${r.element_type}, ${r.match_type}) — ${r.file_path}:${r.start_line}-${r.end_line}${r.signature ? `\n   \`${r.signature}\`` : ''}${r.parent_class ? ` [class: ${r.parent_class}]` : ''}${r.complexity > 0 ? ` [complexity: ${r.complexity}]` : ''}`
            ).join('\n');

            return text(`## Symbols matching "${name}" — ${results.length} found\n\n${lines}\n\n_Use get_code("name") to see the source code._`);
        } catch (err: any) { return error(err.message); }
    }
);

// ═══════════════════════════════════════════════════════════════════════
// TOOL 4: Get Code (FULL source code of a specific element)
// ═══════════════════════════════════════════════════════════════════════

server.registerTool(
    'get_code',
    {
        description: `Get the FULL source code of a specific function, method, or class.

Returns: complete source code, signature, docstring, and metadata.
Use this AFTER search_code or find_symbol to get the actual implementation.

This is the only tool that returns full source code — all others return compact metadata.`,
        inputSchema: {
            name: z.string().describe('Exact function/method/class name to retrieve'),
            repo_name: z.string().optional().describe('Filter by repository name'),
        },
    },
    async ({ name, repo_name }) => {
        try {
            const res = await apiRequest('POST', '/api/mcp/get-code', {
                name, repo_name: repo_name || '',
            });
            if (res.status !== 200) return error(`Failed: ${JSON.stringify(res.data)}`);

            const results = res.data.results || [];
            if (results.length === 0) return text(`No code found for "${name}". Try find_symbol first.`);

            const parts = results.map((r: any) => {
                const header = [
                    `## ${r.qualified_name || r.name} (${r.element_type})`,
                    `**File:** ${r.file_path}:${r.start_line}-${r.end_line}`,
                ];
                if (r.signature) header.push(`**Signature:** \`${r.signature}\``);
                if (r.parent_class) header.push(`**Class:** ${r.parent_class}`);
                if (r.description) header.push(`**Description:** ${r.description}`);
                if (r.docstring) header.push(`**Docstring:** ${r.docstring}`);
                header.push(`**Complexity:** ${r.complexity}`);
                header.push(`\n\`\`\`${r.language || ''}\n${r.code}\n\`\`\``);
                return header.join('\n');
            });

            return text(parts.join('\n\n---\n\n'));
        } catch (err: any) { return error(err.message); }
    }
);

// ═══════════════════════════════════════════════════════════════════════
// TOOL 5: Get Callers (who calls this?)
// ═══════════════════════════════════════════════════════════════════════

server.registerTool(
    'get_callers',
    {
        description: `Find all direct callers of a function or method.

Returns: caller name, type, file, line number. Compact — no source code.
Use this to understand who depends on a function before modifying it.`,
        inputSchema: {
            name: z.string().describe('Function/method name to find callers of'),
            repo_name: z.string().optional().describe('Filter by repository name'),
        },
    },
    async ({ name, repo_name }) => {
        try {
            const res = await apiRequest('POST', '/api/mcp/get-callers', {
                name, repo_name: repo_name || '',
            });
            if (res.status !== 200) return error(`Failed: ${JSON.stringify(res.data)}`);

            const callers = res.data.callers || [];
            if (callers.length === 0) return text(`No callers found for "${name}". It may be a top-level entry point or unused.`);

            const lines = callers.map((c: any, i: number) =>
                `${i + 1}. **${c.caller_qualified_name || c.caller_name}** (${c.caller_type}) — ${c.caller_file}:${c.caller_line}`
            ).join('\n');

            return text(`## Callers of "${name}" — ${callers.length} found\n\n${lines}`);
        } catch (err: any) { return error(err.message); }
    }
);

// ═══════════════════════════════════════════════════════════════════════
// TOOL 6: Get Callees (what does this function call?)
// ═══════════════════════════════════════════════════════════════════════

server.registerTool(
    'get_callees',
    {
        description: `Find all functions/methods that a given function calls.

Returns: callee name, type, file, line number. Compact — no source code.
Use this to understand the dependencies of a function.`,
        inputSchema: {
            name: z.string().describe('Function/method name to find callees of'),
            repo_name: z.string().optional().describe('Filter by repository name'),
        },
    },
    async ({ name, repo_name }) => {
        try {
            const res = await apiRequest('POST', '/api/mcp/get-callees', {
                name, repo_name: repo_name || '',
            });
            if (res.status !== 200) return error(`Failed: ${JSON.stringify(res.data)}`);

            const callees = res.data.callees || [];
            if (callees.length === 0) return text(`"${name}" doesn't call any tracked functions.`);

            const lines = callees.map((c: any, i: number) =>
                `${i + 1}. **${c.callee_qualified_name || c.callee_name}** (${c.callee_type}) — ${c.callee_file}:${c.callee_line}`
            ).join('\n');

            return text(`## Functions called by "${name}" — ${callees.length} found\n\n${lines}`);
        } catch (err: any) { return error(err.message); }
    }
);

// ═══════════════════════════════════════════════════════════════════════
// TOOL 7: Get Impact (what breaks if I change this?)
// ═══════════════════════════════════════════════════════════════════════

server.registerTool(
    'get_impact',
    {
        description: `Impact analysis: what code is affected if a function changes?

Returns:
- Direct callers (functions that directly call this)
- Total transitively affected elements (callers of callers, recursively)
- All affected files

This is the KEY tool for answering: "If I modify function X, what else might break?"`,
        inputSchema: {
            name: z.string().describe('Function/method name to analyze impact of'),
            repo_name: z.string().optional().describe('Filter by repository name'),
        },
    },
    async ({ name, repo_name }) => {
        try {
            const res = await apiRequest('POST', '/api/mcp/get-impact', {
                name, repo_name: repo_name || '',
            });
            if (res.status !== 200) return error(`Failed: ${JSON.stringify(res.data)}`);

            const d = res.data;
            const parts = [
                `## Impact Analysis: "${d.target || name}"`,
                `- **Direct callers:** ${d.direct_callers || 0}`,
                `- **Total affected elements:** ${d.total_affected || 0}`,
                `- **Affected files:** ${(d.affected_files || []).length}`,
            ];

            if (d.affected_files && d.affected_files.length > 0) {
                parts.push(`\n### Affected Files\n${d.affected_files.map((f: string) => `- ${f}`).join('\n')}`);
            }

            if (d.direct_caller_details && d.direct_caller_details.length > 0) {
                parts.push(`\n### Direct Callers`);
                d.direct_caller_details.forEach((c: any, i: number) => {
                    parts.push(`${i + 1}. **${c.caller_qualified_name || c.caller_name}** (${c.caller_type}) — ${c.caller_file}:${c.caller_line}`);
                });
            }

            if (d.all_affected_elements && d.all_affected_elements.length > 0) {
                parts.push(`\n### All Affected Elements (transitive)`);
                d.all_affected_elements.forEach((e: any, i: number) => {
                    parts.push(`${i + 1}. **${e.qualified_name || e.name}** (${e.element_type}) — ${e.file_path}:${e.start_line}`);
                });
            }

            return text(parts.join('\n'));
        } catch (err: any) { return error(err.message); }
    }
);

// ═══════════════════════════════════════════════════════════════════════
// TOOL 8: Get Call Chain (path between two functions)
// ═══════════════════════════════════════════════════════════════════════

server.registerTool(
    'get_call_chain',
    {
        description: `Find the call path between two functions.

Returns the shortest chain of function calls connecting function A to function B.
Example: A → B → C → D

Use this to understand how two distant functions are connected.`,
        inputSchema: {
            from_name: z.string().describe('Source function name (the caller end)'),
            to_name: z.string().describe('Target function name (the callee end)'),
            max_depth: z.number().optional().default(10).describe('Maximum chain depth (default 10)'),
        },
    },
    async ({ from_name, to_name, max_depth }) => {
        try {
            const res = await apiRequest('POST', '/api/mcp/get-call-chain', {
                from_name, to_name, max_depth: max_depth || 10,
            });
            if (res.status !== 200) return error(`Failed: ${JSON.stringify(res.data)}`);

            if (!res.data.found || !res.data.chain || res.data.chain.length === 0) {
                return text(`No call path found from "${from_name}" to "${to_name}".`);
            }

            const chain = res.data.chain[0]?.chain || res.data.chain;
            const chainStr = chain.map((node: any) =>
                `**${node.qualified_name || node.name}** (${node.element_type}) — ${node.file_path}:${node.start_line}`
            ).join('\n  ↓\n');

            return text(`## Call Chain: ${from_name} → ${to_name}\n\n${chainStr}`);
        } catch (err: any) { return error(err.message); }
    }
);

// ═══════════════════════════════════════════════════════════════════════
// TOOL 9: Get File Structure
// ═══════════════════════════════════════════════════════════════════════

server.registerTool(
    'get_file_structure',
    {
        description: `List all functions, methods, and classes defined in a specific file.

Returns: element name, type, line range, signature, complexity, whether it has docstrings.
Use this to understand the structure of a file before reading specific code.

Accepts partial file paths: "server.py" matches "src/code_indexer/api/server.py".`,
        inputSchema: {
            file_path: z.string().describe('File path (can be partial, e.g., "server.py")'),
            repo_name: z.string().optional().describe('Filter by repository name'),
        },
    },
    async ({ file_path, repo_name }) => {
        try {
            const res = await apiRequest('POST', '/api/mcp/file-structure', {
                file_path, repo_name: repo_name || '',
            });
            if (res.status !== 200) return error(`Failed: ${JSON.stringify(res.data)}`);

            const elements = res.data.elements || [];
            if (elements.length === 0) return text(`No code elements found in "${file_path}".`);

            const lines = elements.map((e: any) => {
                const doc = e.has_docstring ? '📝' : '  ';
                return `${doc} **${e.qualified_name || e.name}** (${e.element_type}) — L${e.start_line}-${e.end_line}${e.signature ? ` \`${e.signature}\`` : ''}${e.parent_class ? ` [${e.parent_class}]` : ''}${e.complexity > 0 ? ` [cx:${e.complexity}]` : ''}`;
            }).join('\n');

            return text(`## File: ${file_path} — ${elements.length} elements\n📝 = has docstring\n\n${lines}`);
        } catch (err: any) { return error(err.message); }
    }
);

// ═══════════════════════════════════════════════════════════════════════
// TOOL 10: Find Dead Code
// ═══════════════════════════════════════════════════════════════════════

server.registerTool(
    'find_dead_code',
    {
        description: `Find potentially dead/unused functions and methods.

Returns functions with zero callers in the code graph.
Excludes: main, __init__, test_*, __dunder__ methods.

Useful for code cleanup and understanding which code is actually used.`,
        inputSchema: {
            repo_name: z.string().optional().describe('Filter by repository name'),
        },
    },
    async ({ repo_name }) => {
        try {
            const res = await apiRequest('POST', '/api/mcp/dead-code', {
                name: '', repo_name: repo_name || '',
            });
            if (res.status !== 200) return error(`Failed: ${JSON.stringify(res.data)}`);

            const dead = res.data.dead_code || [];
            if (dead.length === 0) return text('No dead code found. All functions have at least one caller.');

            const lines = dead.map((d: any, i: number) =>
                `${i + 1}. **${d.qualified_name || d.name}** (${d.element_type}) — ${d.file_path}:${d.start_line}${d.complexity > 0 ? ` [complexity: ${d.complexity}]` : ''}`
            ).join('\n');

            return text(`## Potentially Dead Code — ${dead.length} functions\n\nThese functions have no callers in the code graph:\n\n${lines}`);
        } catch (err: any) { return error(err.message); }
    }
);

// ── Start ─────────────────────────────────────────────────────────────

async function main() {
    const transport = new StdioServerTransport();
    await server.connect(transport);
}

main().catch((err) => {
    console.error('MCP server fatal error:', err);
    process.exit(1);
});
