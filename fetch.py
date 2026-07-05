import urllib.request
import re
from html.parser import HTMLParser
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

class HTMLFilter(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []

    def handle_data(self, data):
        data = data.strip()
        if data:
            self.text.append(data)

html = urllib.request.urlopen('https://docs.qcode.cc/docs/usage/adaptive-thinking', context=ctx).read().decode('utf-8')
f = HTMLFilter()
f.feed(html)
print('\n'.join(f.text))
