import unittest
from email.message import EmailMessage

from local_agentic_triage.mail.vulcan import notification_uuid

U1 = '11111111-2222-4333-8444-555555555555'
U2 = '66666666-7777-4888-9999-000000000000'
SAFE = 'invalid notification'


def mail(plain=None, html=None, headers=()):
    message = EmailMessage()
    message['From'] = 'noreply@example.invalid'
    for key, value in headers:
        message[key] = value
    if plain is not None:
        message.set_content(plain)
        if html is not None:
            message.add_alternative(html, subtype='html')
    elif html is not None:
        message.set_content(html, subtype='html')
    return message.as_bytes()


class NotificationUuidTests(unittest.TestCase):
    def rejected(self, raw):
        with self.assertRaises(ValueError) as caught:
            notification_uuid(raw)
        self.assertEqual(str(caught.exception), SAFE)
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)

    def test_plain_text_uuid(self):
        self.assertEqual(notification_uuid(mail(f'Hello\nID: {U1}\nBye')), U1)

    def test_html_only_uuid_with_entities_and_tags(self):
        self.assertEqual(notification_uuid(mail(html=f'<p>Hi</p><div>ID:&nbsp;<b>{U1}</b></div>')), U1)

    def test_same_uuid_across_alternatives(self):
        self.assertEqual(notification_uuid(mail(f'ID: {U1}', f'<p>ID: {U1}</p>')), U1)

    def test_alternative_without_any_uuid_is_ignored(self):
        self.assertEqual(notification_uuid(mail(f'ID: {U1}', '<p>ordinary duplicate representation</p>')), U1)

    def test_hidden_only_html_uuid_is_rejected(self):
        self.rejected(mail(f'ID: {U1}', f'<p>ordinary duplicate representation</p><span title="{U2}">text</span>'))

    def test_encapsulated_message_is_not_a_notification_representation(self):
        raw = (b'Content-Type: multipart/mixed; boundary=outer\r\n\r\n'
               b'--outer\r\nContent-Type: text/plain\r\n\r\nID: ' + U1.encode() + b'\r\n'
               b'--outer\r\nContent-Type: message/rfc822\r\n\r\n'
               b'From: sender@example.invalid\r\n\r\nID: ' + U2.encode() + b'\r\n'
               b'--outer--\r\n')
        self.assertEqual(notification_uuid(raw), U1)

    def test_transfer_encodings_decoded(self):
        raw = (b'Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n\r\n'
               + __import__('base64').b64encode(f'ID: {U1}\n'.encode()) + b'\r\n')
        self.assertEqual(notification_uuid(raw), U1)
        raw = (b'Content-Type: text/plain\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\n'
               + f'ID: {U1}=0A'.encode())
        self.assertEqual(notification_uuid(raw), U1)

    def test_uuid_case_normalized(self):
        self.assertEqual(notification_uuid(mail(f'ID: {U1.upper()}', f'<p>ID: {U1}</p>')), U1)

    def test_rejections(self):
        cases = {
            'missing': mail('nothing here'),
            'empty': b'',
            'two lines same part': mail(f'ID: {U1}\nID: {U1}'),
            'distinct plain/html': mail(f'ID: {U1}', f'<p>ID: {U2}</p>'),
            'extra uuid in text': mail(f'ID: {U1}\nref {U2}'),
            'malformed id': mail('ID: 1111'),
            'trailing junk': mail(f'ID: {U1} extra'),
            'inline not own line': mail(f'see ID: {U1}'),
            'hidden html uuid': mail(html=f'<p>ID: {U1}</p><style>.x{{}}/*{U2}*/</style>'),
            'hidden duplicate html uuid': mail(html=f'<p>ID: {U1}</p><span title="{U1}">ordinary text</span>'),
            'balanced hidden distinct html uuid': mail(
                html=f'<p>ID: {U1[:-12]}<b>{U1[-12:]}</b></p><span title="{U2}">ordinary text</span>'),
            'html comment uuid': mail(html=f'<p>ID: {U1}</p><!-- {U2} -->'),
            'html attribute uuid': mail(html=f'<p title="{U2}">ID: {U1}</p>'),
            'attachment only': (b'Content-Type: application/octet-stream\r\n'
                                b'Content-Disposition: attachment; filename=a.txt\r\n\r\n' + f'ID: {U1}'.encode()),
            'non-text part': b'Content-Type: image/png\r\n\r\n' + f'ID: {U1}'.encode(),
        }
        for name, raw in cases.items():
            with self.subTest(name):
                self.rejected(raw)

    def test_invalid_mime_rejected_without_leaking(self):
        for raw in (b'Content-Type: multipart/mixed\r\n\r\nID: ' + U1.encode(),
                    b'Content-Type: text/plain; charset=nope-private\r\nContent-Transfer-Encoding: base64\r\n\r\n!!!',
                    'ID: private'):
            with self.subTest(raw=raw[:20]):
                self.rejected(raw)


if __name__ == '__main__':
    unittest.main()
