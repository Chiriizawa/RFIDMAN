import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from dotenv import load_dotenv

load_dotenv()

def test_email():
    try:
        smtp_server = os.getenv("MAIL_HOST")
        smtp_port = int(os.getenv("MAIL_PORT"))
        username = os.getenv("MAIL_USERNAME")
        password = os.getenv("MAIL_PASSWORD")
        
        print(f"Testing email configuration...")
        print(f"Server: {smtp_server}:{smtp_port}")
        print(f"Username: {username}")
        
        # Test connection
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(username, password)
        
        print("✓ Email connection successful!")
        
        # Send test email
        msg = MIMEMultipart()
        msg['From'] = username
        msg['To'] = username  # Send to yourself for testing
        msg['Subject'] = "Test Email from Tap & Know"
        
        body = "This is a test email to verify SMTP configuration."
        msg.attach(MIMEText(body, 'plain'))
        
        server.send_message(msg)
        server.quit()
        
        print("✓ Test email sent successfully!")
        return True
        
    except Exception as e:
        print(f"✗ Email test failed: {e}")
        return False

if __name__ == "__main__":
    test_email()