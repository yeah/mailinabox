from time import sleep
import requests
import os
import pytest
import re
import imaplib
import poplib
import smtplib
from email.mime.text import MIMEText

from settings import *
from common import random_id


def new_message(from_email, to_email):
    """Creates an email (headers & body) with a random subject"""
    msg = MIMEText('Testing')
    msg['Subject'] = random_id()
    msg['From'] = from_email
    msg['To'] = to_email
    return msg.as_string(), msg['subject']



def check_imap_received(subject):
    """Connects with IMAP and asserts the existence of an email, then deletes it"""

    sleep(10)

    # Login to IMAP
    m = imaplib.IMAP4_SSL(TEST_DOMAIN, 993)
    m.login(TEST_ADDRESS, TEST_PASSWORD)
    m.select()

    # check the message exists
    typ, data = m.search(None, '(SUBJECT \"{}\")'.format(subject))
    res = len(data[0].split()) == 1

    if res:
        m.store(data[0].strip(), '+FLAGS', '\\Deleted')
        m.expunge()
    m.close()
    m.logout()
    return res


def assert_pop3_received(subject):
    """Connects with POP3S and asserts the existence of an email, then deletes it"""

    sleep(10)

    # Login to POP3
    mail = poplib.POP3_SSL(TEST_DOMAIN, 995)
    mail.user(TEST_ADDRESS)
    mail.pass_(TEST_PASSWORD)

    # Assert the message exists
    num = len(mail.list()[1])
    resp, text, octets = mail.retr(num)
    assert "Subject: " + subject in text

    # Delete it and log out
    mail.dele(num)
    mail.quit()


def test_imap_requires_ssl():
    """IMAP without SSL is NOT available"""
    with pytest.raises(socket.timeout):
        imaplib.IMAP4(TEST_DOMAIN, 143)


def test_pop3_requires_ssl():
    """POP3 without SSL is NOT available"""
    with pytest.raises(socket.timeout):
        poplib.POP3(TEST_DOMAIN, 110)


def test_smtps():
    """Email sent from an MUA via SMTPS is delivered"""
    msg, subject = new_message(TEST_ADDRESS, TEST_ADDRESS)
    s = smtplib.SMTP(TEST_DOMAIN, 587)
    s.starttls()
    s.login(TEST_ADDRESS, TEST_PASSWORD)
    s.sendmail(TEST_ADDRESS, [TEST_ADDRESS], msg)
    s.quit()
    assert check_imap_received(subject)


def test_smtps_tag():
    """Email sent to address with tag is delivered"""
    mail_address = TEST_ADDRESS.replace("@", "+sometag@")
    msg, subject = new_message(TEST_ADDRESS, mail_address)
    s = smtplib.SMTP(TEST_DOMAIN, 587)
    s.starttls()
    s.login(TEST_ADDRESS, TEST_PASSWORD)
    s.sendmail(TEST_ADDRESS, [mail_address], msg)
    s.quit()
    assert check_imap_received(subject)


def test_smtps_requires_auth():
    """SMTPS with no authentication is rejected"""
    import smtplib
    s = smtplib.SMTP(TEST_DOMAIN, 587)
    s.starttls()

    #FIXME why does this work without login?

    with pytest.raises(smtplib.SMTPRecipientsRefused):
        s.sendmail(TEST_ADDRESS, [TEST_ADDRESS], 'Test')

    s.quit()


def test_smtp():
    """Email sent from an MTA is delivered"""
    import smtplib
    msg, subject = new_message(TEST_SENDER, TEST_ADDRESS)
    s = smtplib.SMTP(TEST_DOMAIN, 25)
    s.sendmail(TEST_SENDER, [TEST_ADDRESS], msg)
    s.quit()
    assert check_imap_received(subject)


def test_smtp_tls():
    """Email sent from an MTA via SMTP+TLS is delivered"""
    msg, subject = new_message(TEST_SENDER, TEST_ADDRESS)
    s = smtplib.SMTP(TEST_DOMAIN, 25)
    s.starttls()
    s.sendmail(TEST_SENDER, [TEST_ADDRESS], msg)
    s.quit()
    assert check_imap_received(subject)


def test_smtp_headers():
    """Email sent from an MTA via SMTP+TLS has TLS headers"""
    # Send a message to root
    msg, subject = new_message(TEST_SENDER, TEST_ADDRESS)
    s = smtplib.SMTP(TEST_DOMAIN, 25)
    s.starttls()
    s.sendmail(TEST_SENDER, [TEST_ADDRESS], msg)
    s.quit()

    sleep(10)

    # Get the message
    m = imaplib.IMAP4_SSL(TEST_DOMAIN, 993)
    m.login(TEST_ADDRESS, TEST_PASSWORD)
    m.select()
    _, res = m.search(None, '(SUBJECT \"{}\")'.format(subject))
    _, data = m.fetch(res[0], '(RFC822)')

    regex = re.compile("Received: from .*\n\s+\(using TLS.*with cipher.*bits\)\)")
    assert regex.search(data[0][1]), data[0][1]

    # Clean up
    m.store(res[0].strip(), '+FLAGS', '\\Deleted')
    m.expunge()
    m.close()
    m.logout()


def test_pop3s():
    """Connects with POP3S and asserts the existance of an email"""
    msg, subject = new_message(TEST_ADDRESS, TEST_ADDRESS)
    s = smtplib.SMTP(TEST_DOMAIN, 587)
    s.starttls()
    s.login(TEST_ADDRESS, TEST_PASSWORD)
    s.sendmail(TEST_ADDRESS, [TEST_ADDRESS], msg)
    s.quit()
    assert_pop3_received(subject)
