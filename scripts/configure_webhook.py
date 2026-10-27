"""Inspect/apply a webhook without placing the bot token on the command line."""
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.request

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from run import load_env
from briefbot.telegram import Telegram,TelegramError


def main():
    parser=argparse.ArgumentParser(description='Inspect or configure the SiteBrief Telegram webhook')
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--apply',action='store_true',help='Apply only after the local polling process has stopped')
    args=parser.parse_args()
    load_env(args.env_file)
    token=os.environ.get('BOT_TOKEN','')
    secret=os.environ.get('WEBHOOK_SECRET','')
    base=os.environ.get('PUBLIC_BASE_URL','').rstrip('/')
    if not token or len(secret)<32 or not base.startswith('https://'):
        raise SystemExit('Set BOT_TOKEN, WEBHOOK_SECRET and HTTPS PUBLIC_BASE_URL in the private env file')
    telegram=Telegram(token,None,None)
    expected=base+'/telegram/webhook'
    try:
        me=telegram.call('getMe')
        info=telegram.call('getWebhookInfo')
        if info.get('url') and info['url']!=expected:
            raise SystemExit('Another webhook is already configured. Review it before replacing.')
        print('Bot:',me['username'])
        print('Target:',expected)
        print('Pending updates:',info.get('pending_update_count',0))
        if args.apply:
            with urllib.request.urlopen(base+'/api/health',timeout=15) as response:
                health=json.load(response)
            if not health.get('ok'):
                raise SystemExit('Hosted health check failed; webhook was not changed')
            telegram.call('setWebhook',{'url':expected,'secret_token':secret,'allowed_updates':['message','callback_query'],
                'max_connections':1,'drop_pending_updates':False})
            info=telegram.call('getWebhookInfo')
            if info.get('url')!=expected:
                raise SystemExit('Telegram did not confirm the target webhook')
            print('Webhook configured. Test /resume while the local process is stopped.')
        else:
            print('Read-only check. Use --apply only after deploying and stopping local polling.')
    except TelegramError as exc:
        raise SystemExit('Telegram API error: '+str(exc.code)) from None


if __name__=='__main__':main()
