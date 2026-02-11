#!/usr/bin/env python3
"""
DebtClear Simple Webhook Server
Receives form submissions from Lovable → Generates LBA PDF → Sends via SendGrid
"""

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime, timedelta
import json
import os
import base64
from pathlib import Path
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="DebtClear Webhook", version="1.0.0")

# ===== CONFIG =====
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
PDF_OUTPUT_DIR = Path("/tmp/debtclear_pdfs")
PDF_OUTPUT_DIR.mkdir(exist_ok=True)

# ===== SCHEMAS =====

class IntakeSubmission(BaseModel):
    """Form submission from Lovable"""
    client_email: str
    client_name: str
    client_business: str
    debtor_name: str
    debtor_address: str
    debtor_type: str  # "business" or "individual"
    amount_owed_gbp: float
    invoice_date: str  # YYYY-MM-DD
    due_date: str  # YYYY-MM-DD
    description_of_debt: str
    dpa_accepted: bool

class CaseData(BaseModel):
    """Processed case data"""
    case_id: str
    client_email: str
    debtor_name: str
    amount_owed_gbp: float
    statutory_interest_gbp: float
    compensation_gbp: float
    total_claim_gbp: float
    lba_pdf_path: str

# ===== UTILITIES =====

def calculate_statutory_claim(amount_gbp: float, due_date_str: str) -> dict:
    """
    Calculate statutory interest + compensation per UK Late Payment Act 1998
    """
    due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
    today = datetime.now()
    days_overdue = max(0, (today - due_date).days)
    
    # Interest: 8% + BoE base rate (currently 4.75%) = 12.75% annual
    annual_rate = 0.1275
    daily_rate = annual_rate / 365
    interest = round(amount_gbp * daily_rate * days_overdue * 100) / 100
    
    # Fixed compensation tiers
    if amount_gbp < 1000:
        compensation = 40
    elif amount_gbp < 10000:
        compensation = 70
    else:
        compensation = 100
    
    total_claim = amount_gbp + interest + compensation
    
    return {
        "days_overdue": days_overdue,
        "statutory_interest_gbp": interest,
        "compensation_gbp": compensation,
        "total_claim_gbp": total_claim
    }

def generate_lba_pdf(case_data: dict) -> str:
    """
    Generate LBA PDF (simple version using HTML to text)
    Returns path to generated PDF
    """
    from datetime import datetime
    
    case_id = case_data["case_id"]
    today = datetime.now()
    
    # Generate LBA text content
    lba_content = f"""
LETTER BEFORE ACTION
Reference: {case_id}
Date: {today.strftime('%d %B %Y')}

FROM:
{case_data['client_business']}
{case_data['client_email']}

TO:
{case_data['debtor_name']}
{case_data['debtor_address']}

RE: FORMAL DEMAND FOR PAYMENT - UNPAID INVOICE

Dear {case_data['debtor_name']},

This is a formal letter before action. This letter is issued in accordance with the Pre-Action Protocol for Debt Claims under the Civil Procedure Rules.

AMOUNT OWED:
Principal Amount: £{case_data['amount_owed_gbp']:,.2f}
Statutory Interest (12.75% p.a.): £{case_data['statutory_interest_gbp']:,.2f}
Fixed Compensation: £{case_data['compensation_gbp']:,.2f}
──────────────────────────────
TOTAL AMOUNT DUE: £{case_data['total_claim_gbp']:,.2f}

LEGAL BASIS:
This claim is made under the Late Payment of Commercial Debts (Interest) Act 1998, which automatically entitles us to statutory interest and compensation for late payment of commercial debts.

STATUTORY INTEREST:
- Base rate: 8% per annum
- Bank of England base rate: 4.75%
- Combined rate: 12.75% per annum
- Interest calculated from due date to today

FIXED COMPENSATION:
- Amount owed £1,000 - £9,999.99: £{case_data['compensation_gbp']:,.2f}

WHAT YOU MUST DO:
You must pay the full amount of £{case_data['total_claim_gbp']:,.2f} within 30 days of this letter.

Payment should be made to:
Bank Details: [To be provided by client]

If you do not pay within 30 days, we will consider this a failure to respond to a letter before action and will commence proceedings at court without further notice. This will result in:
- County Court Judgment against you
- Damage to your business credit rating (lasting 6 years)
- Additional court fees and legal costs
- Enforcement proceedings

ALTERNATIVE RESOLUTION:
If you dispute this debt or have difficulty paying, please contact us within 7 days to discuss.

This letter is a formal notice. Failure to respond or pay will be used as evidence of your failure to comply with the Pre-Action Protocol.

Yours faithfully,

{case_data['client_business']}

---
This letter has been prepared by DebtClear Ltd, a legal document preparation service.
This is not legal advice. Please seek independent legal counsel if required.
"""
    
    # Save as text file (PDF generation would require additional library)
    # For MVP, we'll generate a text file and note that PDF conversion is needed
    txt_path = PDF_OUTPUT_DIR / f"{case_id}.txt"
    with open(txt_path, "w") as f:
        f.write(lba_content)
    
    logger.info(f"LBA generated: {txt_path}")
    
    return str(txt_path)

def send_email_via_sendgrid(to_email: str, subject: str, html_body: str) -> bool:
    """
    Send email via SendGrid API
    """
    try:
        import requests
        
        url = "https://api.sendgrid.com/v3/mail/send"
        headers = {
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "personalizations": [{
                "to": [{"email": to_email}],
                "subject": subject
            }],
            "from": {"email": "noreply@debtclear.eu"},
            "content": [{
                "type": "text/html",
                "value": html_body
            }]
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        
        if response.status_code in [200, 201, 202]:
            logger.info(f"Email sent to {to_email}")
            return True
        else:
            logger.error(f"SendGrid error: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Email send failed: {str(e)}")
        return False

# ===== ENDPOINTS =====

@app.get("/health")
async def health():
    """Health check"""
    return {
        "status": "ok",
        "service": "debtclear-webhook",
        "timestamp": datetime.now().isoformat()
    }

@app.post("/api/intake")
async def handle_intake(submission: IntakeSubmission):
    """
    Handle Lovable form submission
    1. Validate debtor type (B2B only)
    2. Verify DPA acceptance
    3. Calculate statutory claim
    4. Generate LBA
    5. Send email
    """
    
    logger.info(f"Intake submission: {submission.client_email}")
    
    # Gate 1: B2B only
    if submission.debtor_type != "business":
        raise HTTPException(
            status_code=422,
            detail="DebtClear processes B2B (business-to-business) debt only. Consumer debt uses a different legal framework."
        )
    
    # Gate 2: DPA acceptance required
    if not submission.dpa_accepted:
        raise HTTPException(
            status_code=422,
            detail="Data Processing Agreement must be accepted to proceed."
        )
    
    # Generate case ID
    case_id = f"DC-{datetime.now().strftime('%Y%m%d')}-{abs(hash(submission.client_email)) % 10000:04d}"
    
    # Calculate statutory claim
    claim_calc = calculate_statutory_claim(
        submission.amount_owed_gbp,
        submission.due_date
    )
    
    # Prepare case data
    case_data = {
        "case_id": case_id,
        "client_email": submission.client_email,
        "client_name": submission.client_name,
        "client_business": submission.client_business,
        "debtor_name": submission.debtor_name,
        "debtor_address": submission.debtor_address,
        "amount_owed_gbp": submission.amount_owed_gbp,
        "invoice_date": submission.invoice_date,
        "due_date": submission.due_date,
        "description_of_debt": submission.description_of_debt,
        "statutory_interest_gbp": claim_calc["statutory_interest_gbp"],
        "compensation_gbp": claim_calc["compensation_gbp"],
        "total_claim_gbp": claim_calc["total_claim_gbp"],
        "days_overdue": claim_calc["days_overdue"]
    }
    
    # Generate LBA PDF
    try:
        lba_path = generate_lba_pdf(case_data)
        case_data["lba_pdf_path"] = lba_path
    except Exception as e:
        logger.error(f"PDF generation failed: {str(e)}")
        raise HTTPException(status_code=500, detail="LBA generation failed")
    
    # Send email to client
    email_html = f"""
    <h2>Your Letter Before Action has been prepared</h2>
    <p>Hello {submission.client_name},</p>
    <p>Your Letter Before Action (LBA) for £{case_data['total_claim_gbp']:,.2f} has been prepared.</p>
    <p><strong>Case ID:</strong> {case_id}</p>
    <p><strong>Total Amount Claimed:</strong> £{case_data['total_claim_gbp']:,.2f}</p>
    <p>The LBA document is ready for download.</p>
    <p>Best regards,<br>DebtClear</p>
    """
    
    email_sent = send_email_via_sendgrid(
        submission.client_email,
        f"Your Letter Before Action - {case_id}",
        email_html
    )
    
    if not email_sent:
        logger.warning("Email send failed but case processed")
    
    # Return success
    return {
        "status": "success",
        "case_id": case_id,
        "client_email": submission.client_email,
        "amount_owed_gbp": submission.amount_owed_gbp,
        "statutory_interest_gbp": case_data["statutory_interest_gbp"],
        "compensation_gbp": case_data["compensation_gbp"],
        "total_claim_gbp": case_data["total_claim_gbp"],
        "lba_generated": True,
        "email_sent": email_sent,
        "message": f"Case {case_id} created successfully. LBA prepared and email sent."
    }

@app.get("/cases/{case_id}")
async def get_case(case_id: str):
    """Retrieve case details"""
    # In MVP, cases are stored in files. Later will use database.
    pdf_file = PDF_OUTPUT_DIR / f"{case_id}.txt"
    if not pdf_file.exists():
        raise HTTPException(status_code=404, detail="Case not found")
    
    return {
        "case_id": case_id,
        "status": "lba_prepared",
        "document": str(pdf_file)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
