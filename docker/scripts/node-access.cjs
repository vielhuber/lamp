let fs = require('node:fs');
let { execFileSync } = require('node:child_process');
let Module = require('node:module');

let identity = process.env.LAMP_ID;
if (!/^[a-f0-9]{12}$/.test(identity || '')) return;
let environment;
try {
    environment = JSON.parse(fs.readFileSync(`/var/lib/lamp/environments/${identity}/environment.json`, 'utf8'));
} catch {
    throw new Error('LAMP Access environment configuration is unavailable.');
}
if (environment.visibility === 'public') return;
let origins = new Set([environment.url, ...(environment.hostnames || []).map(host => `https://${host}`)]
    .map(url => new URL(url).origin).filter(origin => origin.startsWith('https://')));
let credentials;
let privateHeader = name => /^cf-access-client-(id|secret)$/i.test(name);

function accessHeaders(url) {
    if (!origins.has(new URL(url).origin)) return {};
    if (!credentials) {
        try {
            credentials = JSON.parse(execFileSync('python3', ['/opt/lamp/control.py', 'access', identity],
                { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] })).headers;
            for (let name of ['CF-Access-Client-Id', 'CF-Access-Client-Secret']) {
                if (typeof credentials?.[name] !== 'string' || !credentials[name] || /[\r\n\0]/.test(credentials[name])) {
                    throw new Error();
                }
            }
        } catch {
            credentials = undefined;
            throw new Error('LAMP Access credentials are unavailable.');
        }
    }
    return credentials;
}

// Critical fetches its HTML in Node before launching Chromium. Recheck every redirect origin.
let fetch = globalThis.fetch;
globalThis.fetch = async function (input, options) {
    let request = new Request(input, options);
    if (!origins.has(new URL(request.url).origin) || !['GET', 'HEAD'].includes(request.method)) {
        return fetch(input, options);
    }
    for (let redirects = 0; redirects <= 20; redirects++) {
        let headers = new Headers(request.headers);
        for (let name of [...headers.keys()]) if (privateHeader(name)) headers.delete(name);
        for (let [name, value] of Object.entries(accessHeaders(request.url))) headers.set(name, value);
        let response = await fetch(request, { ...options, headers, redirect: 'manual' });
        let location = response.headers.get('location');
        if (![301, 302, 303, 307, 308].includes(response.status) || !location || request.redirect === 'manual') {
            if (redirects) Object.defineProperty(response, 'redirected', { value: true });
            return response;
        }
        await response.body?.cancel();
        if (request.redirect === 'error' || redirects === 20) throw new TypeError('LAMP Access fetch redirect refused.');
        let next = new URL(location, request.url);
        if (!['http:', 'https:'].includes(next.protocol) || next.username || next.password) {
            throw new TypeError('LAMP Access fetch redirect refused.');
        }
        headers = new Headers(request.headers);
        if (next.origin !== new URL(request.url).origin) {
            for (let name of ['authorization', 'cookie', 'proxy-authorization']) headers.delete(name);
        }
        request = new Request(next, { method: request.method, headers, signal: request.signal, redirect: request.redirect });
    }
};

let patched = new WeakSet();
let load = Module._load;
Module._load = function (...parameters) {
    let exports = load.apply(this, parameters);
    let chromium = exports && Object.hasOwn(exports, 'chromium') ? exports.chromium : null;
    if (!chromium?.launch || patched.has(chromium)) return exports;
    patched.add(chromium);
    let launch = chromium.launch;
    chromium.launch = async function (...parameters) {
        let browser = await launch.apply(this, parameters);
        try {
            let session = await browser.newBrowserCDPSession();
            session.on('Fetch.requestPaused', async event => {
                try {
                    let headers = Object.fromEntries(Object.entries(event.request.headers).filter(([name]) => !privateHeader(name)));
                    Object.assign(headers, accessHeaders(event.request.url));
                    await session.send('Fetch.continueRequest', {
                        requestId: event.requestId,
                        headers: Object.entries(headers).map(([name, value]) => ({ name, value: String(value) }))
                    });
                } catch {
                    await session.send('Fetch.failRequest', { requestId: event.requestId, errorReason: 'AccessDenied' }).catch(() => {});
                }
            });
            await session.send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] });
            let newContext = browser.newContext;
            browser.newContext = async function (...parameters) {
                let context = await newContext.apply(this, parameters);
                context.on('request', request => {
                    let headers = request.headers.bind(request);
                    let allHeaders = request.allHeaders.bind(request);
                    let headersArray = request.headersArray.bind(request);
                    let headerValue = request.headerValue.bind(request);
                    request.headers = () => Object.fromEntries(Object.entries(headers()).filter(([name]) => !privateHeader(name)));
                    request.allHeaders = async () => Object.fromEntries(Object.entries(await allHeaders()).filter(([name]) => !privateHeader(name)));
                    request.headersArray = async () => (await headersArray()).filter(header => !privateHeader(header.name));
                    request.headerValue = async name => privateHeader(name) ? null : headerValue(name);
                });
                return context;
            };
            return browser;
        } catch {
            await browser.close();
            throw new Error('LAMP Access browser authentication could not be initialized.');
        }
    };
    return exports;
};
