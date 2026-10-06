"""Render the legacy deployment's HTTPS/authentication boundary without side effects."""
import argparse
import re
from urllib.parse import urlsplit


def public_domain(origin: str) -> str:
    parsed = urlsplit(origin)
    domain = parsed.hostname or ''
    labels = domain.split('.')
    if (origin != f'https://{domain}' or parsed.path or parsed.query or parsed.fragment
            or len(labels) < 2 or len(domain) > 253 or labels[-1].isdigit()
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)):
        raise ValueError('PUBLIC_ORIGIN must be https://your.dns.domain without a port, path or credentials')
    return domain


def render_nginx(origin: str, *, bootstrap: bool = False) -> str:
    domain = public_domain(origin)
    http = f'''server {{
    listen 80;
    server_name {domain};
    location /.well-known/acme-challenge/ {{
        root /var/www/letsencrypt;
    }}
    location / {{
        return 301 https://{domain}$request_uri;
    }}
}}
'''
    if bootstrap:
        return http
    return http + f'''
server {{
    listen 443 ssl;
    server_name {domain};
    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    auth_basic "Final Trade";
    auth_basic_user_file /etc/nginx/final-trade.htpasswd;
    root /opt/final-trade/frontend/dist;
    index index.html;

    location /api/ {{
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
    }}
    location /journal-app/ {{
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }}
    location / {{
        try_files $uri $uri/ /index.html;
    }}
    location /assets/ {{
        expires 30d;
        add_header Cache-Control "public, immutable";
    }}
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml;
    gzip_min_length 1024;
}}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('origin')
    parser.add_argument('--domain-only', action='store_true')
    parser.add_argument('--bootstrap', action='store_true')
    args = parser.parse_args()
    try:
        result = public_domain(args.origin) if args.domain_only else render_nginx(args.origin, bootstrap=args.bootstrap)
    except ValueError as exc:
        parser.error(str(exc))
    print(result)


if __name__ == '__main__':
    main()
