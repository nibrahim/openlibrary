"""Open Library Import API
"""
 
from infogami.plugins.api.code import add_hook
from infogami import config
from openlibrary.plugins.openlibrary.code import can_write
from openlibrary.catalog.marc.marc_binary import MarcBinary
from openlibrary.catalog.marc.marc_xml import MarcXml
from openlibrary.catalog.marc.parse import read_edition
from openlibrary.catalog import add_book
from openlibrary.plugins.importapi.import_edition_builder import import_edition_builder as ebuilder

#import openlibrary.tasks
from ... import tasks

import web

import base64
import json
import pprint
import re
import urllib


import import_opds
import import_rdf
import import_edition_builder
from lxml import etree

def parse_meta_headers(edition_builder):
    # parse S3-style http headers
    # we don't yet support augmenting complex fields like author or language
    # string_keys = ['title', 'title_prefix', 'description']

    re_meta = re.compile('HTTP_X_ARCHIVE_META(?:\d{2})?_(.*)')
    for k, v in web.ctx.env.items():
        m = re_meta.match(k)
        if m:
            meta_key = m.group(1).lower()
            edition_builder.add(meta_key, v, restrict_keys=False)

def parse_data(data):
    data = data.strip()
    if -1 != data[:10].find('<?xml'):
        root = etree.fromstring(data)
        #print root.tag
        if '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF' == root.tag:
            edition_builder = import_rdf.parse(root)
            format = 'rdf'
        elif '{http://www.w3.org/2005/Atom}entry' == root.tag:
            edition_builder = import_opds.parse(root)
            format = 'opds'
        elif '{http://www.loc.gov/MARC21/slim}record' == root.tag:
            if root.tag == '{http://www.loc.gov/MARC21/slim}collection':
                root = root[0]
            rec = MarcXml(root)
            edition = read_edition(rec)
            edition_builder = import_edition_builder.import_edition_builder(init_dict=edition)
            format = 'marcxml'
        else:
            print 'unrecognized XML format'
            return None, None
    elif data.startswith('{') and data.endswith('}'):
        obj = json.loads(data)
        edition_builder = import_edition_builder.import_edition_builder(init_dict=obj)
        format = 'json'
    else:
        #Marc Binary
        if len(data) != int(data[:5]):
            return json.dumps({'success':False, 'error':'Bad MARC length'})
    
        rec = MarcBinary(data)
        edition = read_edition(rec)
        edition_builder = import_edition_builder.import_edition_builder(init_dict=edition)
        format = 'marc'

    parse_meta_headers(edition_builder)
    
    return edition_builder.get_dict(), format

def get_next_count():
    store = web.ctx.site.store
    counter = store.get('import_api_s3_counter')
    print 'counter: ',
    print counter
    if None == counter:
        store['import_api_s3_counter'] = {'count':0}
        return 0
    else:
        count = counter['count'] + 1
        store['import_api_s3_counter'] = {'count':count, '_rev':counter['_rev']}
        return count

def queue_s3_upload(data, format):
    s3_key = config.plugin_importapi.get('s3_key')
    s3_secret = config.plugin_importapi.get('s3_secret')
    counter = get_next_count()
    filename = '%03d.%s' % (counter, format)
    s3_item_id = config.plugin_importapi.get('s3_item', 'test_ol_import')
    s3_item_id += '_%03d' % (counter/1000)

    #print 'attempting to queue s3 upload with %s:%s file=%s item=%s' % (s3_key, s3_secret, filename, s3_item_id)
    tasks.upload_via_s3.delay(s3_item_id, filename, data, s3_key, s3_secret)
    #print 'done queuing s3 upload'

    source_url = 'http://www.archive.org/download/%s/%s' % (s3_item_id, filename)
    return source_url

class importapi:
    def GET(self):
        web.header('Content-Type', 'text/plain')
        tasks.add.delay(777, 777)
        return 'Import API only supports POST requests.'

    def POST(self):
        web.header('Content-Type', 'application/json')

        if not can_write():
            return json.dumps({'success':False, 'error':'Permission Denied'})

        data = web.data()
       
        edition, format = parse_data(data)
        #print edition

        source_url = None
        if 'source_records' not in edition:
            source_url = queue_s3_upload(data, format)
            edition['source_records'] = [source_url]

        #call Edward's code here with the edition dict
        if edition:
            reply = add_book.load(edition)
            if source_url:
                reply['source_record'] = source_url
            return json.dumps(reply)
        else:
            return json.dumps({'success':False, 'error':'Failed to parse Edition data'})


class ils_search:
    """Search and Import API to use in Koha. 
    
    When a new catalog record is added to Koha, it makes a request with all
    the metadata to find if OL has a matching record. OL returns the OLID of
    the matching record if exists, if not it creates a new record and returns
    the new OLID.
    
    Request Format:
    
        POST /api/ils_search
        Content-type: application/json
        Authorization: Basic base64-of-username:password
    
        {
            "title": "",
            "author": "",
            "publisher": "...",
            "publish_year": "...",
            "isbn": [...],
            "lccn": [...],
        }
        
    Response Format:
    
        {
            'status': 'found | notfound | created',
            'olid': 'OL12345M',
            'key': '/books/OL12345M',
            'cover': {
                'small': 'http://covers.openlibrary.org/b/12345-S.jpg',
                'medium': 'http://covers.openlibrary.org/b/12345-M.jpg',
                'large': 'http://covers.openlibrary.org/b/12345-L.jpg',
            },
            ...
        }
        
    When authorization header is not provided and match is not found,
    status='notfound' is returned instead of creating a new record.
    """
    def POST(self):
        rawdata = json.loads(web.data())

        # step 1: prepare the data
        data = self.prepare_data(rawdata)
    
        # step 2: search 
        key = self.search(data)

        # step 3: if no match found, create it
        if not key:
            self.create_entry(data)
        else:
            print "Didn't create. We already found something ",key
        
        # step 4: format the result
        # doc = key and web.ctx.site.get(key).dict()
        # d = self.format_result(doc)
        return json.dumps({})
        
    def prepare_data(self, rawdata):
        data = dict(rawdata)
        isbns = data.pop("isbn", None)
        if isbns:
            data['isbn_13'] = [str(n) for n in isbns if len(str(n).replace("-", "")) == 13]
            data['isbn_10'] = [str(n) for n in isbns if len(str(n).replace("-", "")) != 13]
        return data
        
    def search(self, record):
        matches = add_book.find_editions(record)
        if matches:
            return matches

    def create_entry(self, data):
        api_dict = ebuilder()
        # Add source record
        isbn = data.get("isbn_10","") or data.get("isbn_13","")
        isbn = ",".join(isbn)
        source = "Koha:"+isbn
        api_dict.add('source_record', source)
        # Add the authors
        for a in data.get("authors",[]):
            print "Adding %s", a
            api_dict.add_author(None, a)
        # Add rest of the fields
        for field in ["title", "publisher", "publish_date", "isbn_10", "isbn_13"]:
            val = data.get(field)
            if val:
                if isinstance(val, list):
                    for i in val:
                        api_dict.add(field, i)
                else:
                    api_dict.add(field, val)

        print "Dict to load is ", api_dict.get_dict()
        add_book.load(api_dict.get_dict())

    def format_result(self, doc):
        if doc:
            d = {
                'status': 'found',
                'key': doc['key'],
                'olid': doc['key'].split("/")[-1]
            }
            
            covers = doc.get('covers') or []
            if covers and covers[0] > 0:
                d['cover'] = {
                    "small": "http://covers.openlibrary.org/b/id/%s-S.jpg" % covers[0],
                    "medium": "http://covers.openlibrary.org/b/id/%s-M.jpg" % covers[0],
                    "large": "http://covers.openlibrary.org/b/id/%s-L.jpg" % covers[0],
                }
        else:
            d = {
                'status': 'notfound'
            }
        return d
        
def http_basic_auth():
    auth = web.ctx.env.get('HTTP_AUTHORIZATION')
    return auth and web.lstrips(auth, "")
        
def basicauth():
    auth = web.ctx.env.get('HTTP_AUTHORIZATION') or web.input(authorization=None, _method="GET").authorization
    if auth:
        username,password = base64.decodestring(auth).split(':')
        return None
        
class ils_cover_upload:
    """Cover Upload API for Koha.
    
    Request Format: Following input fields with enctype multipart/form-data
    
        * authorization: base64 of username:password
        * olid: Key of the edition. e.g. OL12345M
        * file: file
        * redirect_url: URL to redirect after upload

    On Success: Redirect to redirect_url?status=ok
    
    On Failure: Redirect to redirect_url?status=error&reason=bad+olid
    """
    def POST(self):
        i = web.input(authorization=None, olid=None, file={}, redirect_url=None, url="")
        
        def error(reason):
            if i.redirect_url:
                url = self.build_url(i.redirect_url, status="error", reason=reason)
                return web.seeother(url)
            else:
                return web.HTTPError("400 Bad Request", {"Content-type": "text/html"}, reason)
                
        def success():
            if i.redirect_url:
                url = self.build_url(i.redirect_url, status="ok")
                return web.seeother(url)
            else:
                return web.ok("done!")
                
        if not i.olid:
            error("olid missing")
            
        key = '/books/' + i.olid
        book = web.ctx.site.get(key)
        if not book:
            raise error("bad olid")
            
        from openlibrary.plugins.upstream import covers
        add_cover = covers.add_cover()
        
        data = add_cover.upload(key, i)
        coverid = data.get('id')
        
        if coverid:
            add_cover.save(book, coverid)
            raise success()
        else:
            raise error("upload failed")
    
    def build_url(self, url, **params):
        if '?' in url:
            return url + "&" + urllib.urlencode(params)    
        else:
            return url + "?" + urllib.urlencode(params)

add_hook("import", importapi)
add_hook("ils_search", ils_search)
add_hook("ils_cover_upload", ils_cover_upload)
