"""Isolated local HTTPS/login/proxy smoke using an explicit Caddy executable.

Requires the development-only cryptography package and curl; no system trust,
hosts file, public certificate, user server, or existing data is modified.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--caddy', type=Path, required=True)
    args = parser.parse_args()
    caddy = args.caddy.resolve()
    curl = shutil.which('curl.exe' if os.name == 'nt' else 'curl')
    if not caddy.is_file() or not curl:
        parser.error('Caddy executable and curl are required')
    output = Path(tempfile.mkdtemp(prefix='trade-proxy-smoke-'))
    api_port, public_port = port(), port()
    while public_port == api_port:
        public_port = port()
    hostname = 'trade.example.com'
    origin = f'https://{hostname}:{public_port}'
    token, password = secrets.token_urlsafe(32), secrets.token_urlsafe(20)
    hashed = subprocess.check_output([str(caddy), 'hash-password', '--plaintext', password], text=True).strip()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(key, hashes.SHA256()))
    cert_path, key_path = output / 'server.pem', output / 'server-key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    config = (ROOT / 'deploy/rebuild/Caddyfile').read_text(encoding='utf-8')
    config = config.replace('{$TRADE_PUBLIC_ORIGIN} {', '{$TRADE_PUBLIC_ORIGIN} {\n'
        + f'    bind 127.0.0.1\n    tls "{cert_path.as_posix()}" "{key_path.as_posix()}"')
    config = '{\n    admin off\n    auto_https disable_redirects\n}\n' + config.replace('127.0.0.1:8011', f'127.0.0.1:{api_port}')
    config_path = output / 'Caddyfile'
    config_path.write_text(config, encoding='utf-8')
    env = {**os.environ, 'TRADE_PUBLIC_ORIGIN': origin, 'TRADE_PROXY_TOKEN': token,
           'TRADE_LOGIN_USER': 'smoke', 'TRADE_LOGIN_PASSWORD_HASH': hashed,
           'TRADE_REBUILD_PORT': str(api_port), 'TRADE_REBUILD_DATA_DIR': str(output / 'data'),
           'XDG_DATA_HOME': str(output / 'xdg-data'), 'XDG_CONFIG_HOME': str(output / 'xdg-config'),
           'APPDATA': str(output / 'app-data'), 'LOCALAPPDATA': str(output / 'local-data')}
    jobs, logs = [], []
    def spawn(command, cwd=None):
        log = (output / f'process-{len(jobs)}.log').open('wb'); logs.append(log)
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        jobs.append(process)
        return process
    def https(path, *, auth=None, cookie=True, method='GET', headers=None, payload=None):
        command = [curl, '--silent', '--show-error', '--max-time', '15', '--noproxy', '*',
            '--resolve', f'{hostname}:{public_port}:127.0.0.1', '--cacert', str(cert_path),
            '--write-out', '\n%{http_code}', '--request', method, origin + path]
        if auth is not None:
            command += ['--user', 'smoke:' + auth]
        if cookie:
            command += ['--cookie', str(output / 'cookies.txt'), '--cookie-jar', str(output / 'cookies.txt')]
        for key, value in (headers or {}).items():
            command += ['--header', key + ': ' + value]
        if payload is not None:
            command += ['--header', 'Content-Type: application/json', '--data-binary', json.dumps(payload)]
        raw = subprocess.check_output(command).decode('utf-8')
        body, code = raw.rsplit('\n', 1)
        return int(code), body
    try:
        validation = subprocess.run([str(caddy), 'validate', '--config', str(config_path)], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
        if validation.returncode:
            raise RuntimeError(validation.stdout.decode('utf-8', errors='replace'))
        spawn([sys.executable, '-m', 'uvicorn', 'trade_app.main:app', '--host', '127.0.0.1', '--port', str(api_port), '--no-proxy-headers'], ROOT / 'backend')
        spawn([str(caddy), 'run', '--config', str(config_path)])
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(150):
            if any(job.poll() is not None for job in jobs):
                raise RuntimeError('Owned process stopped; inspect ' + str(output))
            try:
                with opener.open(f'http://127.0.0.1:{api_port}/health', timeout=.3) as response:
                    assert response.status == 200
                with socket.create_connection(('127.0.0.1', public_port), timeout=.3):
                    pass
                break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Owned process startup timed out')
        for path in ('/rebuild.html', '/health', '/api/v1/session'):
            assert https(path, cookie=False)[0] == 401
        assert https('/api/v1/session', auth='wrong', cookie=False)[0] == 401
        status, body = https('/api/v1/session', auth=password)
        assert status == 200
        csrf = json.loads(body)['data']['csrf_token']
        cookie_lines = (output / 'cookies.txt').read_text().splitlines()
        assert any('\tTRUE\t' in row and 'trade_rebuild_sid' in row for row in cookie_lines)
        assert https('/api/v1/accounts', auth=password, method='POST', payload={'name': '必须校验CSRF'})[0] == 403
        headers = {'Origin': origin, 'X-CSRF-Token': csrf, 'Idempotency-Key': secrets.token_hex(16), 'X-Trade-Proxy-Token': 'forged'}
        status, body = https('/api/v1/accounts', auth=password, method='POST', headers=headers, payload={'name': '本机HTTPS代理验收'})
        assert status == 200, body  # Proxy replaces a client-supplied fake internal token.
        assert https('/api/v1/accounts', auth=password, headers={'Origin': 'https://evil.example'})[0] == 403
        assert https('/rebuild.html', auth=password)[0] == 200
        assert len(json.loads(https('/api/v1/accounts', auth=password)[1])['data']) == 1
        print(json.dumps({'status': 'passed', 'evidence': str(output), 'caddy': subprocess.check_output([str(caddy), 'version'], text=True).strip(),
                          'checks': ['CA_verified_local_TLS', 'all_paths_require_login', 'wrong_password_401', 'secure_cookie', 'CSRF', 'origin', 'proxy_header_override', 'authenticated_read_write']}, ensure_ascii=False))
    finally:
        for job in reversed(jobs):
            if job.poll() is None:
                job.terminate()
                try:
                    job.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    job.kill(); job.wait(timeout=5)
        for log in logs:
            log.close()


if __name__ == '__main__':
    main()
