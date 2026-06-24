const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const readline = require('readline');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const SESSION_DIR  = path.join(__dirname, '.wwebjs_auth', 'session-ringalert');
const SESSION_DATA = path.join(SESSION_DIR, 'Default');

function hasActiveSession() {
    return fs.existsSync(SESSION_DATA);
}

function askQuestion(prompt) {
    return new Promise(resolve => {
        const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
        rl.question(prompt, answer => { rl.close(); resolve(answer.trim().toLowerCase()); });
    });
}

function deleteSession() {
    if (fs.existsSync(SESSION_DIR)) {
        fs.rmSync(SESSION_DIR, { recursive: true, force: true });
        console.log('Sesion anterior eliminada.\n');
    }
}

function cleanupLockFiles() {
    try {
        execSync('taskkill /F /IM chrome.exe /T', { stdio: 'pipe' });
        console.log('Procesos Chrome previos terminados.');
    } catch (e) { /* sin procesos Chrome activos */ }

    const lockFiles = ['SingletonLock', 'SingletonCookie', 'SingletonSocket', 'DevToolsActivePort'];
    for (const file of lockFiles) {
        try { fs.rmSync(path.join(SESSION_DIR, file), { force: true }); } catch (e) { /* ignorar */ }
    }
}

async function main() {
    console.log('');
    console.log('================================================================');
    console.log('  RingAlert - Configuracion de sesion WhatsApp');
    console.log('================================================================');
    console.log('');

    if (hasActiveSession()) {
        console.log('Se encontro una sesion de WhatsApp activa.');
        const answer = await askQuestion('Desea iniciar una nueva sesion? (reemplaza la actual) [s/n]: ');

        if (answer !== 's' && answer !== 'si' && answer !== 'sí') {
            console.log('\nOperacion cancelada. La sesion actual se mantiene.');
            process.exit(0);
        }

        console.log('\nEliminando sesion anterior...');
        deleteSession();
    } else {
        console.log('No se encontro sesion activa. Iniciando autenticacion...\n');
    }

    cleanupLockFiles();
    console.log('\nIniciando WhatsApp... Por favor espera.\n');

    const client = new Client({
        authStrategy: new LocalAuth({ clientId: 'ringalert' }),
        puppeteer: {
            executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
            headless: true,
            args: ['--no-sandbox', '--disable-setuid-sandbox']
        }
    });

    client.on('qr', qr => {
        console.log('\n--- Escanea este QR con el WhatsApp del numero secundario ---\n');
        qrcode.generate(qr, { small: true });
        console.log('\nEsperando escaneo del QR...\n');
    });

    client.on('ready', async () => {
        console.log('');
        console.log('================================================================');
        console.log('  Sesion iniciada correctamente. WhatsApp conectado.');
        console.log('  Puedes cerrar esta ventana y reiniciar el servicio RingAlert.');
        console.log('================================================================');
        console.log('');
        await client.destroy();
        process.exit(0);
    });

    client.on('auth_failure', msg => {
        console.error(`\nError de autenticacion: ${msg}`);
        console.error('Intenta ejecutar este script nuevamente.\n');
        process.exit(1);
    });

    await client.initialize();
}

main().catch(err => {
    console.error('Error inesperado:', err.message);
    process.exit(1);
});
