let assert = require('node:assert/strict');
let fs = require('node:fs');
let https = require('node:https');
let http = require('node:http');
let tls = require('node:tls');
let os = require('node:os');
let path = require('node:path');
let child = require('node:child_process');
let { test, mock } = require('node:test');

test('Node and Chromium authenticate only the environment HTTPS origins, including assets and redirects', async () => {
    let temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'lamp-node-access-'));
    let servers = [];
    let browser;
    try {
        child.execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
            '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1',
            '-keyout', `${temporary}/key`, '-out', `${temporary}/cert`], { stdio: 'ignore' });
        let certificate = fs.readFileSync(`${temporary}/cert`);
        let certificates = tls.getCACertificates('default');
        tls.setDefaultCACertificates([...certificates, certificate.toString()]);
        let options = { key: fs.readFileSync(`${temporary}/key`), cert: certificate };
        let foreignRequests = [];
        let localRequests = [];
        let foreign = https.createServer(options, (request, response) => {
            foreignRequests.push(request.headers);
            response.end('foreign');
        });
        let insecure = http.createServer((request, response) => {
            foreignRequests.push(request.headers);
            response.end('insecure');
        });
        servers.push(foreign, insecure);
        for (let server of servers) await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
        let destination = `https://127.0.0.1:${foreign.address().port}`;
        let insecureDestination = `http://127.0.0.1:${insecure.address().port}`;
        let local = https.createServer(options, (request, response) => {
            localRequests.push({ url: request.url, headers: request.headers });
            if (request.headers['cf-access-client-secret'] !== 'fixture-secret') {
                response.writeHead(403);
                response.end('Access required');
                return;
            }
            let redirects = { '/redirect': destination, '/insecure': insecureDestination, '/same': '/asset' };
            if (redirects[request.url]) {
                response.writeHead(302, { Location: redirects[request.url] });
                response.end();
                return;
            }
            if (request.url === '/sw.js') {
                response.setHeader('Content-Type', 'application/javascript');
                response.end("self.addEventListener('install', event => event.waitUntil(fetch('/worker').then(() => self.skipWaiting())));");
                return;
            }
            response.setHeader('Content-Type', 'text/html');
            response.end(request.url === '/' ? '<h1>Protected page</h1><img src="/asset"><iframe src="/frame"></iframe>' : 'authenticated');
        });
        servers.push(local);
        await new Promise(resolve => local.listen(0, '127.0.0.1', resolve));
        let origin = `https://127.0.0.1:${local.address().port}`;
        let read = fs.readFileSync;
        let execute = child.execFileSync;
        process.env.LAMP_ID = 'abcdef012345';
        mock.method(fs, 'readFileSync', (file, ...parameters) => file === '/var/lib/lamp/environments/abcdef012345/environment.json'
            ? JSON.stringify({ url: origin, hostnames: [], visibility: 'private' }) : read(file, ...parameters));
        mock.method(child, 'execFileSync', (command, parameters, options) => parameters[0] === '/opt/lamp/control.py'
            ? JSON.stringify({ headers: { 'CF-Access-Client-Id': 'fixture-id', 'CF-Access-Client-Secret': 'fixture-secret' } })
            : execute(command, parameters, options));
        require('../scripts/node-access.cjs');
        for (let [url, text] of [[origin, 'Protected page'], [`${origin}/same`, 'authenticated'],
            [`${origin}/redirect`, 'foreign'], [`${origin}/insecure`, 'insecure'], [destination, 'foreign']]) {
            let response = await fetch(url);
            assert.equal(response.status, 200);
            assert.ok((await response.text()).includes(text));
        }
        assert.equal((await fetch(`${origin}/redirect`, { redirect: 'manual' })).status, 302);
        await assert.rejects(fetch(`${origin}/redirect`, { redirect: 'error' }), /redirect refused/);
        let globalModules = child.execFileSync('/opt/lamp/node-tools/npm', ['root', '-g'], { encoding: 'utf8' }).trim();
        let { chromium } = await import(`${globalModules}/playwright/index.mjs`);
        browser = await chromium.launch({ args: ['--no-sandbox', '--ignore-certificate-errors'] });
        let context = await browser.newContext({ ignoreHTTPSErrors: true });
        let observed = [];
        context.on('requestfinished', request => observed.push(request));
        let page = await context.newPage();
        await page.goto(origin);
        assert.equal(await page.locator('h1').textContent(), 'Protected page');
        await page.evaluate(async () => {
            await fetch('/api');
            await navigator.serviceWorker.register('/sw.js');
            await navigator.serviceWorker.ready;
        });
        await page.goto(`${origin}/redirect`);
        assert.equal(page.url(), `${destination}/`);
        for (let request of observed) {
            assert.equal(request.headers()['cf-access-client-secret'], undefined);
            assert.equal((await request.allHeaders())['cf-access-client-secret'], undefined);
            assert.equal(await request.headerValue('CF-Access-Client-Secret'), null);
        }
        for (let url of ['/', '/asset', '/frame', '/api', '/worker']) {
            let request = localRequests.find(item => item.url === url);
            assert.ok(request, url);
            assert.equal(request.headers['cf-access-client-id'], 'fixture-id', url);
            assert.equal(request.headers['cf-access-client-secret'], 'fixture-secret', url);
        }
        assert.ok(foreignRequests.length >= 4);
        for (let headers of foreignRequests) {
            assert.equal(headers['cf-access-client-id'], undefined);
            assert.equal(headers['cf-access-client-secret'], undefined);
        }
        tls.setDefaultCACertificates(certificates);
    } finally {
        await browser?.close();
        for (let server of servers) {
            server.closeAllConnections();
            await new Promise(resolve => server.close(resolve));
        }
        mock.restoreAll();
        fs.rmSync(temporary, { recursive: true, force: true });
    }
});
