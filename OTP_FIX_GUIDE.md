# OTP Email Sending - Fix Summary & Debugging Guide

## ✅ Issues Fixed

### 1. **Async/Sync Mismatch in Email Function**
   - **Problem**: `send_otp_email()` was declared as `async` but performed blocking SMTP operations, causing event loop issues
   - **Solution**: Created `_send_otp_email_sync()` for blocking operations and wrapped it with `run_in_executor()` in the async wrapper

### 2. **Missing Error Handling & Logging**
   - **Problem**: Insufficient debugging information when email sending failed
   - **Solution**: Added comprehensive debug logging marked with `[OTP_EMAIL]`, `[OTP_STORE]`, `[OTP_VERIFY]` tags throughout the flow

### 3. **SMTP Configuration Not Validated**
   - **Problem**: No way to verify SMTP settings are correct
   - **Solution**: Added debug endpoints to test SMTP configuration and send test emails

## 📝 Added Debug Logging Tags

All debug logs use specific prefixes for easy filtering:
- `[OTP_EMAIL]` - Email sending operations
- `[OTP_STORE]` - OTP storage in Firestore
- `[OTP_VERIFY]` - OTP verification
- `[OTP_CLEANUP]` - Cleanup operations
- `[SEND_OTP]` - Send OTP endpoint flow
- `[VERIFY_OTP]` - Verify OTP endpoint flow
- `[DEBUG_SMTP]` - SMTP diagnostics

## 🔧 Debug/Test Endpoints

### Test SMTP Configuration
```bash
POST /auth/debug/test-smtp
# Attempts to send test email to test@example.com
# Check logs to see SMTP connection details
```

### Get Current SMTP Configuration
```bash
GET /auth/debug/smtp-config
# Returns configured SMTP settings (password hidden)
```

## ⚙️ Setup SMTP Configuration

### Gmail (Recommended)
1. Enable 2-Factor Authentication on your Google Account
2. Generate App Password: https://myaccount.google.com/apppasswords
3. Create `.env` file with:
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=xxxx xxxx xxxx xxxx
SMTP_FROM_NAME=Smart PDF
SMTP_FROM_EMAIL=your-email@gmail.com
```

### Other Providers
- **SendGrid**: Host=smtp.sendgrid.net, Port=587, User=apikey, Password=<api-key>
- **AWS SES**: Host=email-smtp.<region>.amazonaws.com, Port=587
- **Mailgun**: Host=smtp.mailgun.org, Port=587

## 🐛 Debugging OTP Flow

### Enable Debug Logging
Check application logs for these log patterns:

1. **Request received**:  
   `[SEND_OTP] Request received for email: user@example.com`

2. **OTP Generated**:  
   `[SEND_OTP] Generated OTP for user@example.com: 123456`

3. **SMTP Connection**:  
   `[OTP_EMAIL] SMTP Config - Host: smtp.gmail.com, Port: 587`

4. **Email Sent**:  
   `[OTP_EMAIL] OTP email sent successfully to user@example.com`

### Common Issues & Fixes

| Issue | Cause | Fix |
|-------|-------|-----|
| SMTP credentials not configured | Missing .env vars | Create .env with SMTP_USER and SMTP_PASSWORD |
| Authentication failed | Wrong app password | Use App Password from Google, not account password |
| Connection timeout | Wrong SMTP_HOST/PORT | Verify host and port match your email provider |
| TLS error | SMTP configuration | Ensure TLS is enabled on port 587 |
| Email loop block | Too many failed attempts | Wait 1 hour for IP block to clear |

## 📊 OTP Lifecycle with Logging

```
1. User requests OTP → [SEND_OTP] Request received
2. Generate OTP → [SEND_OTP] Generated OTP
3. Store in Firestore → [OTP_STORE] Storing OTP
4. Send email via SMTP → [OTP_EMAIL] Starting email send, [OTP_EMAIL] Email sent successfully
5. User verifies → [VERIFY_OTP] Verification request
6. Check Firestore → [OTP_VERIFY] OTP record found
7. Match OTP → [OTP_VERIFY] OTP verified successfully
```

## 🔍 Quick Troubleshooting

If OTP emails aren't arriving:

1. **Check logs for `[OTP_EMAIL]` errors**:
   ```bash
   # Look for these in your application logs
   grep "OTP_EMAIL" application.log
   ```

2. **Test SMTP with debug endpoint**:
   ```bash
   curl -X POST http://localhost:10000/auth/debug/test-smtp
   ```

3. **Verify SMTP configuration**:
   ```bash
   curl http://localhost:10000/auth/debug/smtp-config
   ```

4. **Check email spam folder** - Sometimes emails are mislabeled

5. **Verify .env file is loaded** - Check app startup logs for SMTP configuration

## ✨ Improvements Made

- ✅ Proper async/await handling with ThreadPoolExecutor
- ✅ Comprehensive logging at every step
- ✅ Better SMTP error messages (authentication, connection, etc.)
- ✅ 10-second SMTP connection timeout to prevent hanging
- ✅ Debug endpoints for diagnostics
- ✅ Firestore error handling with logging
- ✅ Cleaner OTP cleanup with logging
