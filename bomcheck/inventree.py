"""Read-only InvenTree REST client, using Python's standard library."""
import json
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward API credentials to redirects (including login pages).
        return None


class InvenTree:
    def __init__(self, url, token, timeout=20):
        self.base = url.rstrip('/') + '/'
        parsed = urlsplit(self.base)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('Invalid InvenTree URL: use the server root without credentials')
        self.api = urljoin(self.base, 'api/')
        self.token, self.timeout = token, timeout
        self.opener = build_opener(NoRedirect())
        self.cache = {}

    def get(self, path, **params):
        url = urljoin(self.api, path)
        if params:
            url += '?' + urlencode(params)
        # Pagination may return absolute links. Only the configured API is trusted.
        target, origin = urlsplit(url), urlsplit(self.api)
        if (target.scheme, target.netloc) != (origin.scheme, origin.netloc) or not target.path.startswith(origin.path):
            raise ValueError('API link points outside the configured server')
        if url not in self.cache:
            request = Request(url, headers={'Authorization': 'Token ' + self.token, 'Accept': 'application/json'})
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    self.cache[url] = json.load(response)
            except HTTPError as error:
                raise ValueError(f'InvenTree HTTP {error.code}: check the URL, API token and read permissions') from None
            except (URLError, TimeoutError, OSError):
                raise ValueError('InvenTree is unreachable: check the network and URL') from None
            except (ValueError, UnicodeError):
                raise ValueError('InvenTree returned non-JSON data: check the API URL') from None
        return self.cache[url]

    def list(self, path, **params):
        result, seen = [], set()
        page = self.get(path, **params)
        while isinstance(page, dict):
            if not isinstance(page.get('results'), list):
                raise ValueError('Invalid API list response')
            result.extend(page['results'])
            next_url = page.get('next')
            if not next_url:
                return result
            if next_url in seen:
                raise ValueError('Cyclic API pagination')
            seen.add(next_url)
            page = self.get(next_url)
        if not isinstance(page, list):
            raise ValueError('Invalid API list response')
        return result + page

    def find(self, kind, code):
        if kind == 'part_id':
            if not str(code).isdigit() or int(code) <= 0:
                raise ValueError('Invalid InvenTree part ID')
            return [self.get(f'part/{int(code)}/')]
        if kind == 'ipn':
            # search is fuzzy on the server; confirm exact IPN locally.
            return [p for p in self.list('part/', search=code) if p.get('IPN') == code]
        matches = self.list('company/part/manufacturer/', MPN=code)
        ids = sorted({m['part'] for m in matches if m.get('MPN') == code})
        return [self.get(f'part/{pk}/') for pk in ids]

    def related(self, pk):
        return self.list('part/', related=pk)

    def available(self, pk):
        data = self.get(f'part/{pk}/requirements/')
        try:
            amount = Decimal(str(data['unallocated_stock']))
            if not amount.is_finite():
                raise ValueError()
            return max(Decimal(0), amount)
        except (KeyError, InvalidOperation, ValueError):
            raise ValueError(f'Invalid available quantity for part {pk}') from None

    def suggestions(self, value):
        return self.list('part/', search=value, active='true')[:8] if value else []
