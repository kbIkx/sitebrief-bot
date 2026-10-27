"""Prepare a private configuration on the host from an uploaded token env file."""
import argparse
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from run import load_env


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-env',type=Path,required=True)
    parser.add_argument('--private-dir',type=Path,required=True)
    parser.add_argument('--base-url',required=True)
    args=parser.parse_args()
    origin=urlsplit(args.base_url)
    if origin.scheme!='https' or not origin.hostname or origin.username or origin.path not in {'','/'} or origin.query or origin.fragment:
        raise SystemExit('Use an HTTPS origin without a path or credentials')
    load_env(args.source_env)
    token=os.environ.get('BOT_TOKEN','')
    if not token:
        raise SystemExit('No BOT_TOKEN in the private source env file')
    private=args.private_dir.resolve()
    private.mkdir(parents=True,exist_ok=True)
    data=private/'data'
    data.mkdir(exist_ok=True)
    path=private/'.env'
    if path.exists():
        raise SystemExit('Target env already exists; refusing to overwrite credentials')
    password=os.environ.get('ADMIN_PASSWORD') or secrets.token_urlsafe(24)
    secret=secrets.token_urlsafe(36)
    body={'BOT_TOKEN':token,'MANAGER_CHAT_ID':os.environ.get('MANAGER_CHAT_ID',''),
          'ADMIN_PASSWORD':password,'WEBHOOK_SECRET':secret,'PUBLIC_BASE_URL':args.base_url.rstrip('/'),
          'DATA_DIR':str(data)}
    if any('\n' in value or '\r' in value for value in body.values()):
        raise SystemExit('Unexpected newline in configuration')
    path.write_text(''.join(k+'='+v+'\n' for k,v in body.items()),encoding='utf-8')
    path.chmod(0o600)
    passfile=private/'admin-password.txt'
    passfile.write_text(password,encoding='utf-8')
    passfile.chmod(0o600)
    print('Private configuration prepared:',path)
    print('Admin password file:',passfile)
    print('No secret values printed. Configure the WSGI entrypoint, then verify /api/health.')


if __name__=='__main__':main()
