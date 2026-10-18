"""Container readiness check; keep the secret URL out of health check logs."""
import os
import sys
from urllib.request import urlopen

from server import DATA


def main():
    try:
        path = os.environ.get('APP_PATH', '').strip('/')
        if not path:
            path = (DATA / 'access-path').read_text().strip()
        port = int(os.environ.get('PORT', '8080'))
        with urlopen(f'http://127.0.0.1:{port}/{path}/api/health', timeout=3) as response:
            return 0 if response.status == 200 else 1
    except Exception:
        return 1


if __name__ == '__main__':
    sys.exit(main())
