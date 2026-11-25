#!/usr/bin/env node
/**
 * Zero-Downtime Blue-Green Router Proxy for LabOps Control Center
 * - Public Frontend listener: 0.0.0.0:3000 -> 127.0.0.1:(3001|3002)
 * - Backend API listener:     127.0.0.1:8000 -> 127.0.0.1:(8001|8002)
 * - Loopback control API:     127.0.0.1:3099 (/status, /switch)
 *
 * Uses only Node.js built-in modules so zero external dependencies or sudo are required.
 */

import http from 'node:http';
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';

const PROJECT_DIR = path.resolve(import.meta.dirname, '..');
const RUNTIME_DIR = path.join(PROJECT_DIR, '.runtime');
const STATE_FILE = path.join(RUNTIME_DIR, 'active-slots.json');

const FRONTEND_PUBLIC_PORT = Number(process.env.LABOPS_PROXY_FRONTEND_PORT || 3000);
const BACKEND_PUBLIC_PORT = Number(process.env.LABOPS_PROXY_BACKEND_PORT || 8000);
const CONTROL_PORT = Number(process.env.LABOPS_PROXY_CONTROL_PORT || 3099);

function loadState() {
  const defaults = {
    frontendSlot: 'blue',
    frontendPort: 3001,
    backendSlot: 'blue',
    backendPort: 8001,
    updatedAt: new Date().toISOString()
  };
  try {
    if (fs.existsSync(STATE_FILE)) {
      const parsed = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
      return {
        ...defaults,
        ...parsed,
        frontendPort: Number(parsed.frontendPort || defaults.frontendPort),
        backendPort: Number(parsed.backendPort || defaults.backendPort)
      };
    }
  } catch (err) {
    console.error('[bluegreen-proxy] Failed to load state file:', err);
  }
  return defaults;
}

function saveState(nextState) {
  fs.mkdirSync(RUNTIME_DIR, { recursive: true });
  const tmpFile = `${STATE_FILE}.tmp.${process.pid}`;
  fs.writeFileSync(tmpFile, JSON.stringify(nextState, null, 2));
  fs.renameSync(tmpFile, STATE_FILE);
}

let activeSlots = loadState();

function createReverseProxy(getTargetPort, label) {
  const server = http.createServer((req, res) => {
    const targetPort = getTargetPort();
    const headers = { ...req.headers };
    const remoteIp = req.socket.remoteAddress || '127.0.0.1';
    headers['x-forwarded-for'] = headers['x-forwarded-for']
      ? `${headers['x-forwarded-for']}, ${remoteIp}`
      : remoteIp;
    if (!headers['x-forwarded-host'] && headers.host) {
      headers['x-forwarded-host'] = headers.host;
    }

    const proxyReq = http.request(
      {
        hostname: '127.0.0.1',
        port: targetPort,
        path: req.url,
        method: req.method,
        headers
      },
      (proxyRes) => {
        res.writeHead(proxyRes.statusCode || 502, proxyRes.statusMessage, proxyRes.headers);
        proxyRes.pipe(res, { end: true });
      }
    );

    proxyReq.setTimeout(120000, () => {
      proxyReq.destroy(new Error('Upstream request timeout'));
    });

    proxyReq.on('error', (err) => {
      console.error(`[${label} -> 127.0.0.1:${targetPort}] Upstream error:`, err.message);
      if (!res.headersSent) {
        res.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(
          JSON.stringify({
            error: 'Upstream temporarily unavailable',
            targetPort,
            detail: err.message
          })
        );
      } else {
        res.destroy();
      }
    });

    req.on('aborted', () => {
      proxyReq.destroy();
    });

    req.pipe(proxyReq, { end: true });
  });

  server.on('upgrade', (req, socket, head) => {
    const targetPort = getTargetPort();
    const upstream = net.connect(targetPort, '127.0.0.1', () => {
      const headerLines = [`${req.method} ${req.url} HTTP/${req.httpVersion}`];
      for (let i = 0; i < req.rawHeaders.length; i += 2) {
        headerLines.push(`${req.rawHeaders[i]}: ${req.rawHeaders[i + 1]}`);
      }
      upstream.write(`${headerLines.join('\r\n')}\r\n\r\n`);
      if (head && head.length > 0) upstream.write(head);
      upstream.pipe(socket);
      socket.pipe(upstream);
    });

    upstream.on('error', () => {
      socket.destroy();
    });
    socket.on('error', () => {
      upstream.destroy();
    });
  });

  return server;
}

const frontendServer = createReverseProxy(() => activeSlots.frontendPort, 'frontend-proxy');
const backendServer = createReverseProxy(() => activeSlots.backendPort, 'backend-proxy');

const controlServer = http.createServer((req, res) => {
  if (req.method === 'GET' && req.url === '/status') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, activeSlots }));
    return;
  }

  if (req.method === 'POST' && req.url === '/switch') {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      try {
        const body = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
        const nextState = {
          frontendSlot: body.frontendSlot || activeSlots.frontendSlot,
          frontendPort: Number(body.frontendPort || activeSlots.frontendPort),
          backendSlot: body.backendSlot || activeSlots.backendSlot,
          backendPort: Number(body.backendPort || activeSlots.backendPort),
          updatedAt: new Date().toISOString()
        };
        if (
          ![3001, 3002].includes(nextState.frontendPort) ||
          ![8001, 8002].includes(nextState.backendPort)
        ) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: 'Invalid Blue-Green slot port' }));
          return;
        }
        activeSlots = nextState;
        saveState(activeSlots);
        console.log(
          `[bluegreen-proxy] Switched cleanly -> FE ${activeSlots.frontendSlot}:${activeSlots.frontendPort}, BE ${activeSlots.backendSlot}:${activeSlots.backendPort}`
        );
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, activeSlots }));
      } catch (err) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: String(err) }));
      }
    });
    return;
  }

  res.writeHead(404, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ error: 'Not Found' }));
});

frontendServer.listen(FRONTEND_PUBLIC_PORT, '0.0.0.0', () => {
  console.log(
    `[bluegreen-proxy] Frontend listening on 0.0.0.0:${FRONTEND_PUBLIC_PORT} -> 127.0.0.1:${activeSlots.frontendPort} (${activeSlots.frontendSlot})`
  );
});

backendServer.listen(BACKEND_PUBLIC_PORT, '127.0.0.1', () => {
  console.log(
    `[bluegreen-proxy] Backend listening on 127.0.0.1:${BACKEND_PUBLIC_PORT} -> 127.0.0.1:${activeSlots.backendPort} (${activeSlots.backendSlot})`
  );
});

controlServer.listen(CONTROL_PORT, '127.0.0.1', () => {
  console.log(`[bluegreen-proxy] Control API listening on 127.0.0.1:${CONTROL_PORT}`);
});

function shutdown() {
  console.log('[bluegreen-proxy] Shutting down gracefully...');
  frontendServer.close();
  backendServer.close();
  controlServer.close();
  setTimeout(() => process.exit(0), 2000).unref();
}

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
