# DebtClear Webhook

Intake webhook for Lovable forms → LBA generation → SendGrid email delivery

## Setup

```bash
git clone https://github.com/geirtellefsen1/debtclear-webhook.git
cd debtclear-webhook
pip install -r requirements.txt
export SENDGRID_API_KEY=your_sendgrid_key_here
python3 -m uvicorn debtclear_webhook:app --host 0.0.0.0 --port 8001
```

## Deploy to DigitalOcean

See DEPLOYMENT_INSTRUCTIONS.md
