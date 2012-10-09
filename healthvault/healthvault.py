#
# healthvault: A Pythonic Interface to the Microsoft HealthVault's API
#
# Arjun Sanyal <arjun.sanyal@childrens.harvard.edu>
#
# Requires:
# - pycrypto > 2.5 (for signatures)
# - lxml
# - cssselect
#
# Notes
# - method names match the HV API but with a leading lowercase letter
# - requires a settings.py file with:
#   - APP_ID
#   - APP_PUBLIC_KEY
#   - APP_PRIVATE_KEY
#   - APP_THUMBPRINT
#   - HV_SERVICE_SERVER
#
# TODO
# - refactor xml creation using lxml's E-factory?
#   <http://lxml.de/tutorial.html#the-e-factory>
# - add newlines and spacing to the templates

import base64
from   Crypto.Signature import PKCS1_v1_5
from   Crypto.Hash import SHA
from   Crypto.PublicKey import RSA
import datetime
import hashlib
import hmac
import httplib
from   lxml import etree
from   lxml.cssselect import CSSSelector as csss
import pdb
import re
import settings
import socket
import string

DEBUG = True
LOG_XML = True

class HVPerson(object):
    person_id = None
    name = None
    selected_record_id = None
    gender = None # m or f
    birth_year = None
    weights = [] # a list of (datetime string, value in kg)

class HVConn(object):
    _user_auth_token = None
    _auth_token = None
    _record_id = None
    _app_specific_record_id = None
    _shared_secret = None
    _private_key = None
    _version = '2.0.0.0'
    _language = 'en'
    _country = 'US'
    _ttl = '1800'

    person = HVPerson()

    def _pretty_print_tree(self, tree):
        print etree.tostring(tree, pretty_print=True)

    def _now_in_iso(self):
        return datetime.datetime.utcnow().isoformat()

    def _init_private_key(self):
        self._private_key = RSA.construct((
            long(settings.APP_PUBLIC_KEY, 16),
            long(65537),
            long(settings.APP_PRIVATE_KEY, 16)
        ))

    def _sign(self, data):
        signer = PKCS1_v1_5.new(self._private_key)
        return base64.encodestring(signer.sign(SHA.new(data)))

    def _send_request(self, payload):
        conn = httplib.HTTPSConnection(settings.HV_SERVICE_SERVER, 443)
        conn.putrequest('POST', '/platform/wildcat.ashx')
        conn.putheader('Content-Type', 'text/xml')
        conn.putheader('Content-Length', '%d' % len(payload))
        conn.endheaders()
        try:
            conn.send(payload)
        except socket.error, v:
            if v[0] == 32:      # Broken pipe
                conn.close()
            raise
        resp = conn.getresponse()
        if resp.status != 200:
            raise
        else:
            return resp

    def _send_request_and_get_tree(self, payload):
        conn = httplib.HTTPSConnection(settings.HV_SERVICE_SERVER, 443)
        conn.putrequest('POST', '/platform/wildcat.ashx')
        conn.putheader('Content-Type', 'text/xml')
        conn.putheader('Content-Length', '%d' % len(payload))
        conn.endheaders()
        try:
            conn.send(payload)
        except socket.error, v:
            if v[0] == 32:      # Broken pipe
                conn.close()
            raise
        resp = conn.getresponse()
        if resp.status != 200:
            raise
        else:
            tree = etree.fromstring(resp.read())
            if DEBUG and LOG_XML:
                print self._pretty_print_tree(tree)

            if csss('code')(tree)[0].text != '0':
                raise 'Non-zero return code in _send_request_and_get_tree()'
            else:
                return tree

    def _authenticate(self):
        content_tmpl = string.Template("""
            <content>
                <app-id>$APP_ID</app-id>
                <hmac>HMACSHA256</hmac>
                <signing-time>$NOW</signing-time>
            </content>
        """)

        content_str = content_tmpl.substitute({
            'APP_ID': settings.APP_ID,
            'NOW': self._now_in_iso()
        }).translate(None, '\n ')

        t = string.Template("""
            <wc-request:request xmlns:wc-request="urn:com.microsoft.wc.request">
                <header>
                    <method>CreateAuthenticatedSessionToken</method>
                    <method-version>2</method-version>
                    <app-id>$APP_ID</app-id>
                    <msg-time>$NOW</msg-time>
                    <msg-ttl>$MSG_TTL</msg-ttl>
                    <version>$VERSION</version>
                </header>
                <info>
                    <auth-info>
                        <app-id>$APP_ID</app-id>
                        <credential>
                            <appserver2>
                                <sig digestMethod="SHA1"
                                     sigMethod="RSA-SHA1"
                                     thumbprint="$APP_THUMBPRINT">
                                        $SIGNATURE
                                </sig>
                                $CONTENT
                            </appserver2>
                        </credential>
                    </auth-info>
                </info>
            </wc-request:request>""")

        payload = t.substitute({
            'APP_ID': settings.APP_ID,
            'NOW': self._now_in_iso(),
            'MSG_TTL': self._ttl,
            'VERSION': self._version,
            'APP_THUMBPRINT': settings.APP_THUMBPRINT,
            'SIGNATURE': self._sign(content_str),
            'CONTENT': content_str

        }).translate(None, '\n')

        tree = self._send_request_and_get_tree(payload)
        self._auth_token = csss('token')(tree)[0].text
        self._shared_secret = csss('shared-secret')(tree)[0].text

    def __init__(self, user_auth_token=None):
        self._init_private_key()
        self._authenticate()

        if user_auth_token:
            self._user_auth_token = str(user_auth_token)
            self.getPersonInfo()

    def _build_header(self, method, method_version, hash_data, record_id=None):
        """ Create the header string. record_id is optional """
        header = '<header><method>$METHOD</method><method-version>$METHOD_VERSION</method-version>'
        if record_id:
            header = header + '<record-id>'+record_id+'</record-id>'
        header = header + '<auth-session><auth-token>$AUTH_TOKEN</auth-token><user-auth-token>$USER_AUTH_TOKEN</user-auth-token></auth-session><language>$LANGUAGE</language><country>$COUNTRY</country><msg-time>$NOW</msg-time><msg-ttl>$TTL</msg-ttl><version>$VERSION</version><info-hash><hash-data algName="SHA256">$HASH_DATA</hash-data></info-hash></header>'
        return string.Template(header).substitute({
            'METHOD': method,
            'METHOD_VERSION': method_version,
            'AUTH_TOKEN': self._auth_token,
            'USER_AUTH_TOKEN': self._user_auth_token,
            'LANGUAGE': self._language,
            'COUNTRY': self._country,
            'NOW': self._now_in_iso(),
            'TTL': self._ttl,
            'VERSION': self._version,
            'HASH_DATA': hash_data,
            'RECORD_ID': record_id
        })

    def _build_header_hmac(self, header):
        h = hmac.new(base64.b64decode(self._shared_secret),
                     header,
                     hashlib.sha256)
        return base64.b64encode(h.digest())

    def _build_request(self, header, info):
        # NOTE: don't add spaces and newlines here!
        req_tmpl = string.Template("""<wc-request:request xmlns:wc-request="urn:com.microsoft.wc.request"><auth><hmac-data algName="HMACSHA256">$HMAC</hmac-data></auth>$HEADER$INFO</wc-request:request>""")
        return req_tmpl.substitute({
            'HMAC': self._build_header_hmac(header),
            'HEADER': header,
            'INFO': info
        })

    def _create_request(self, info, method, method_version='1', record_id=None):
        hash_data = base64.b64encode(hashlib.sha256(info).digest()).strip()
        header = self._build_header(method=method,
                                    method_version=method_version,
                                    hash_data=hash_data,
                                    record_id=record_id)
        return self._build_request(header, info)

    def getPersonInfo(self):
        # TODO: extract more record data
        # <record app-record-auth-action="NoActionRequired"
        # app-specific-record-id="218697"
        # auth-expires="9999-12-31T23:59:59.999Z"
        # date-created="2012-09-19T16:07:52.507Z"
        # date-updated="2012-09-24T19:22:00.877Z" display-name="Arjun"
        # id="f9982b79-4369-4357-8268-0b344941ab02" location-country="US"
        # max-size-bytes="4294967296" record-custodian="true" rel-name="Self"
        # rel-type="1" size-bytes="3167" state="Active">

        tree = self._send_request_and_get_tree(
            self._create_request('<info></info>', 'GetPersonInfo')
        )


        self.person.person_id = csss('person-id')(tree)[0].text
        self.person.name = csss('name')(tree)[0].text
        self.person.selected_record_id = csss('selected-record-id')(tree)[0].text
        self._record_id = self.person.selected_record_id

    def getThings(self, type):
        info = '<info><group><filter><type-id>' \
                + type \
                + '</type-id></filter><format></format></group></info>'
        return self._send_request_and_get_tree(
            self._create_request(info,
                                 'GetThings',
                                 record_id=self._record_id)
        )

    def getThingById(self, id):
        # Note: this is also a 'GetThings' call with additional data
        # in the <info> element. The <xml/> is required eventhough
        # it's marked at <xml />* in the spec. Spec bug?
        info = '<info><group><id>' \
                + id \
                +'</id><format><section>core</section><xml/></format></group></info>'

        return self._send_request_and_get_tree(
            self._create_request(info,
                                 'GetThings',
                                 record_id=self._record_id)
        )

    def getBasicDemographicInformation(self):
        tree = self.getThings('bf516a61-5252-4c28-a979-27f45f62f78d')

        for id in [t.text for t in csss('thing-id')(tree)]:
            tree = self.getThingById(id)
            self.person.gender = csss('gender')(tree)[0].text
            self.person.birth_year = csss('birthyear')(tree)[0].text

    def getWeightMeasurements(self):
        tree = self.getThings('3d34d87e-7fc1-4153-800f-f56592cb0d17')
        self.person.weights = []  # clear weights

        for id in [t.text for t in csss('thing-id')(tree)]:
            tree = self.getThingById(id)
            date = csss('date')(tree)[0]
            time = csss('time')(tree)[0]
            y   = int(csss('y')(date)[0].text)
            m   = int(csss('m')(date)[0].text)
            d   = int(csss('d')(date)[0].text)
            h   = int(csss('h')(time)[0].text)
            min = int(csss('m')(time)[0].text)
            s   = int(csss('s')(time)[0].text)

            dt = datetime.datetime(y, m, d, h, min, s).isoformat()
            weight_in_kg = float(csss('kg')(tree)[0].text)
            self.person.weights.append((dt, round(weight_in_kg, 2)))

    def createConnectRequest(self, external_id, friendly_name, secret_q, secret_a):
        info_tmpl = string.Template('<info><friendly-name>$FRIENDLY_NAME</friendly-name><question>$QUESTION</question><answer>$ANSWER</answer><external-id>$EXTERNAL_ID</external-id></info>')
        info = info_tmpl.substitute({
            'FRIENDLY_NAME': friendly_name,
            'QUESTION': secret_q,
            'ANSWER': secret_a,
            'EXTERNAL_ID': external_id
        })

        tree = self._send_request_and_get_tree(
            self._create_request(info,
                                 'CreateConnectRequest',
                                 record_id=self._record_id)
        )

        tree = etree.fromstring(tree.toxml())
        return csss('identity-code')(tree)[0].text
