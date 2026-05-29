const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const http = require('http');
const fs = require('fs');

let client = null;
let isReady = false;

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
    console.log('\n📱 Escanea este QR con tu WhatsApp secundario:');
    qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
    isReady = true;
    console.log('\n✅ WhatsApp conectado y listo!');
});

// Cola de mensajes entrantes
const inbox = [];

// Escuchar mensajes entrantes
client.on('message', msg => {
    console.log(`📩 Mensaje recibido de ${msg.from}: ${msg.body}`);
    inbox.push({
        from: msg.from,
        body: msg.body,
        timestamp: Date.now()
    });
    // Mantener solo los últimos 50 mensajes
    if (inbox.length > 50) inbox.shift();
});

client.on('disconnected', reason => {
    isReady = false;
    console.log('⚠️ WhatsApp desconectado:', reason);
});

client.initialize();

// Servidor HTTP para recibir órdenes desde Python
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

                // Formatear número (ej: 5212223334444@c.us)
                const number = to.replace(/[^0-9]/g, '') + '@c.us';

                // Enviar texto primero
                await client.sendMessage(number, message);
                console.log(`📤 Texto enviado a ${to}`);

                // Enviar imagen si existe
                if (imagePath && fs.existsSync(imagePath)) {
                    const media = MessageMedia.fromFilePath(imagePath);
                    await client.sendMessage(number, media);
                    console.log(`📸 Foto enviada a ${to}`);
                }

                res.writeHead(200);
                res.end(JSON.stringify({ ok: true }));

            } catch (err) {
                console.error('❌ Error:', err.message);
                res.writeHead(500);
                res.end(JSON.stringify({ ok: false, error: err.message }));
            }
        });

    } else if (req.url === '/status') {
        res.writeHead(200);
        res.end(JSON.stringify({ ok: true, connected: isReady }));

    } else if (req.url === '/inbox' && req.method === 'GET') {
        const messages = inbox.splice(0, inbox.length); // vaciar y retornar
        res.writeHead(200);
        res.end(JSON.stringify({ ok: true, messages }));

    } else {
        res.writeHead(404);
        res.end();
    }
});

server.listen(3000, '127.0.0.1', () => {
    console.log('✅ Servidor HTTP escuchando en puerto 3000');
    console.log('⏳ Iniciando WhatsApp...\n');
});