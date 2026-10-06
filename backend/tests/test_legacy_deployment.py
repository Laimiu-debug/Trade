"""The generated legacy proxy exposes applications only behind HTTPS and auth."""
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('legacy_nginx', ROOT / 'deploy/legacy_nginx.py')
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


@pytest.mark.parametrize('origin', ['', 'http://trade.example.com', 'https://127.0.0.1',
    'https://trade.example.com/', 'https://trade.example.com:443', 'https://user@trade.example.com',
    'https://trade.example.com?secret=1', 'https://trade.example.com#fragment',
    'https://trade..example.com', 'https://-trade.example.com', 'https://trade.example.com\n',
    'https://trade.example.com\r', 'https://trade.example.com\n; config'])
def test_public_origin_rejects_ambiguous_or_injectable_configuration(origin):
    with pytest.raises(ValueError):
        config.public_domain(origin)


def test_proxy_bootstrap_never_serves_application_and_https_protects_both_workspaces():
    origin = 'https://trade.example.com'
    assert config.public_domain(origin) == 'trade.example.com'
    bootstrap = config.render_nginx(origin, bootstrap=True)
    assert 'acme-challenge' in bootstrap and 'return 301 https://trade.example.com$request_uri' in bootstrap
    assert 'proxy_pass' not in bootstrap and 'frontend/dist' not in bootstrap
    rendered = config.render_nginx(origin)
    assert rendered.startswith(bootstrap)
    assert 'listen 443 ssl;' in rendered and 'ssl_certificate_key ' in rendered
    assert 'auth_basic "Final Trade";' in rendered and 'auth_basic_user_file ' in rendered
    assert rendered.count('proxy_pass http://127.0.0.1:8000;') == 2
    assert 'location /api/' in rendered and 'location /journal-app/' in rendered
    script = (ROOT / 'deploy/deploy.sh').read_text(encoding='utf-8')
    assert 'Environment=TRADING_MS_ALLOWED_ORIGINS=${PUBLIC_ORIGIN}' in script
    assert script.index('--bootstrap |') < script.index('sudo certbot certonly') < script.index('"${PUBLIC_ORIGIN}" | sudo tee')
