import sys

try:
    import win32com.client
    OUTLOOK_AVAILABLE = True
except ImportError:
    OUTLOOK_AVAILABLE = False


def create_draft_email(to_email, cc_email, subject, body_html):
    """
    Uses local Windows Outlook application to create and open a draft email.
    """
    if not OUTLOOK_AVAILABLE:
        return False, "pywin32 is not installed. Please run: pip install pywin32"
    
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        mail = outlook.CreateItem(0)  # 0 = olMailItem
        mail.To = to_email
        if cc_email:
            mail.CC = cc_email
        mail.Subject = subject
        mail.HTMLBody = body_html
        mail.Display(False)  # Shows the draft to the user
        return True, ""
    except Exception as e:
        return False, f"Failed to create Outlook email: {str(e)}"
