const esbuild = require('esbuild');

const isWatch = process.argv.includes('--watch');
const isProd = !isWatch;

/** @type {import('esbuild').BuildOptions} */
const sharedOptions = {
    bundle: true,
    platform: 'node',
    target: 'node20',
    format: 'cjs',
    minify: isProd,
    sourcemap: isProd ? false : 'linked',
    legalComments: 'none',
};

const extensionBuild = {
    ...sharedOptions,
    entryPoints: ['./src/extension.ts'],
    outfile: './dist/extension.js',
    external: ['vscode'],
};

const mcpServerBuild = {
    ...sharedOptions,
    entryPoints: ['./src/mcp-server.ts'],
    outfile: './dist/mcp-server.js',
    format: 'esm',
    banner: {
        // Shim require() for ESM context (needed by some MCP SDK internals)
        js: `
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
        `.trim(),
    },
};

async function build() {
    try {
        if (isWatch) {
            const extCtx = await esbuild.context(extensionBuild);
            const mcpCtx = await esbuild.context(mcpServerBuild);
            await extCtx.watch();
            await mcpCtx.watch();
            console.log('[esbuild] watching for changes...');
        } else {
            await esbuild.build(extensionBuild);
            await esbuild.build(mcpServerBuild);
            console.log('[esbuild] build complete (minified)');
        }
    } catch (e) {
        console.error(e);
        process.exit(1);
    }
}

build();
