"""Container entrypoint behind the hosting provider's HTTPS reverse proxy.

Telegram uses polling; no webhook or local computer is needed.
Keep .env and data private. Expose only the provider's HTTPS domain.
"""
import os
from pathlib import Path
import sys


def main():
    from run import main as run
    root = Path(__file__).resolve().parent
    # Providers set SERVER_PORT; explicit arguments keep a copied local PORT
    # or HOST value from overriding the container's allocation.
    port = os.environ.get('SERVER_PORT') or os.environ.get('PORT') or '8765'
    sys.argv = [str(root / 'run.py'),
                '--env-file', str(root / '.env'),
                '--data-dir', os.environ.get('DATA_DIR', str(root / 'data')),
                '--host', os.environ.get('HOST', '0.0.0.0'),
                '--port', port, '--secure-cookies', *sys.argv[1:]]
    run()


if __name__ == '__main__':
    main()
