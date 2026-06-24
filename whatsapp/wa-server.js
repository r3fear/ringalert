const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// Logger con marca de tiempo estandarizada (mismo formato que ringalert.log)
function log(level, message) {
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const ts = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ` +
               `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
    console.log(`${ts} - ${level} - ${message}`);
}

let client = null;
let isReady = false;

// Cache @lid -> numero de telefono, construido al registrar destinatarios
const lidToNumber = {};

// Busca en el historial del chat de cada numero los mensajes entrantes con @lid
async function buildLidMap(numbers) {
    for (const number of numbers) {
        try {
            const chat = await client.getChatById(number + '@c.us');
            const messages = await chat.fetchMessages({ limit: 10 });
            for (const m of messages) {
                if (!m.fromMe && m.from && m.from.endsWith('@lid')) {
                    const lid = m.from.split('@')[0];
                    if (!lidToNumber[lid]) {
                        lidToNumber[lid] = number;
                        log('INFO', `@lid resuelto: ${lid} -> ${number}`);
                    }
                    break;
                }
            }
        } catch (e) {
            // Chat no encontrado o sin historial previo
        }
    }
}

// Iniciar cliente WhatsApp
client = new Client({
    authStrategy: new LocalAuth({ clientId: 'ringalert' }),
    puppeteer: {
        executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    }
});

client.on('qr', qr => {
    log('INFO', 'Escanea el QR con el WhatsApp secundario:');
    qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
    isReady = true;
    log('INFO', 'WhatsApp conectado y listo');
});

// Cola de mensajes entrantes
const inbox = [];

// Escuchar mensajes entrantes
client.on('message', msg => {
    let senderNumber = msg.from;
    if (msg.from.endsWith('@lid')) {
        const lid = msg.from.split('@')[0];
        senderNumber = lidToNumber[lid] || lid;
    } else if (msg.from.endsWith('@c.us')) {
        senderNumber = msg.from.split('@')[0];
    }
    log('INFO', `Mensaje recibido de ${senderNumber}: ${msg.body}`);
    inbox.push({
        from: senderNumber,
        body: msg.body,
        timestamp: Date.now()
    });
    if (inbox.length > 50) inbox.shift();
});

client.on('disconnected', reason => {
    isReady = false;
    log('WARNING', `WhatsApp desconectado: ${reason}`);
});

// Elimina archivos de bloqueo de Chrome/Puppeteer que quedan tras un apagado inesperado.
// Primero termina procesos Chrome huérfanos (que impiden borrar el SingletonLock en Windows),
// luego elimina los archivos. Usa __dirname para rutas absolutas independientes del CWD.
function cleanupLockFiles() {
    // 1. Matar procesos Chrome que hayan quedado vivos del arranque anterior.
    //    execSync falla con código 128 si no hay procesos — eso es normal, se ignora.
    try {
        execSync('taskkill /F /IM chrome.exe /T', { stdio: 'pipe' });
        log('INFO', 'Procesos Chrome previos terminados');
    } catch (e) {
        // No había procesos Chrome activos
    }

    // 2. Eliminar archivos de bloqueo con ruta absoluta basada en __dirname
    const sessionDir = path.join(__dirname, '.wwebjs_auth', 'session-ringalert');
    const lockFiles = ['SingletonLock', 'SingletonCookie', 'SingletonSocket', 'DevToolsActivePort'];
    let removed = 0;
    try {
        for (const file of lockFiles) {
            const fullPath = path.join(sessionDir, file);
            if (fs.existsSync(fullPath)) {
                fs.rmSync(fullPath, { force: true });
                removed++;
            }
        }
        if (removed > 0) {
            log('INFO', `Archivos de bloqueo eliminados: ${removed}`);
        }
    } catch (e) {
        log('ERROR', `Error limpiando archivos de bloqueo: ${e.message}`);
    }
}

cleanupLockFiles();
client.initialize();

// Servidor HTTP para recibir ordenes desde Python
const server = http.createServer(async (req, res) => {

    if (req.method === 'POST' && req.url === '/send') {
        let body = '';
        req.on('data', chunk => body += chunk);
        req.on('end', async () => {
            try {
                const data = JSON.parse(body);
                const { to, message, imagePath } = data;

                if (!isReady) {
                    res.writeHead(503);
                    res.end(JSON.stringify({ ok: false, error: 'WhatsApp no conectado' }));
                    return;
                }

                // Si ya es un JID completo (contiene @) usarlo directo; si no, agregar @c.us
                const number = to.includes('@') ? to : (to.replace(/[^0-9]/g, '') + '@c.us');

                await client.sendMessage(number, message);
                log('INFO', `Texto enviado a ${to}`);

                if (imagePath && fs.existsSync(imagePath)) {
                    const media = MessageMedia.fromFilePath(imagePath);
                    await client.sendMessage(number, media);
                    log('INFO', `Foto enviada a ${to}`);
                }

                res.writeHead(200);
                res.end(JSON.stringify({ ok: true }));

            } catch (err) {
                log('ERROR', `Error en /send: ${err.message}`);
                res.writeHead(500);
                res.end(JSON.stringify({ ok: false, error: err.message }));
            }
        });

    } else if (req.method === 'POST' && req.url === '/register-recipients') {
        let body = '';
        req.on('data', chunk => body += chunk);
        req.on('end', async () => {
            try {
                const { numbers } = JSON.parse(body);
                if (isReady && numbers && numbers.length) {
                    await buildLidMap(numbers);
                }
                res.writeHead(200);
                res.end(JSON.stringify({ ok: true, mapped: Object.keys(lidToNumber).length }));
            } catch (err) {
                log('ERROR', `Error en /register-recipients: ${err.message}`);
                res.writeHead(500);
                res.end(JSON.stringify({ ok: false, error: err.message }));
            }
        });

    } else if (req.url === '/status') {
        res.writeHead(200);
        res.end(JSON.stringify({ ok: true, connected: isReady }));

    } else if (req.url === '/inbox' && req.method === 'GET') {
        const messages = inbox.splice(0, inbox.length);
        res.writeHead(200);
        res.end(JSON.stringify({ ok: true, messages }));

    } else {
        res.writeHead(404);
        res.end();
    }
});

server.listen(3000, '127.0.0.1', () => {
    log('INFO', 'Servidor HTTP escuchando en puerto 3000');
    log('INFO', 'Iniciando WhatsApp...');
});
