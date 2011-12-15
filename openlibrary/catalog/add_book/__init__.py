from openlibrary.catalog.merge.merge_marc import build_marc
from infogami import config
from load_book import build_query, import_author, east_in_by_statement, InvalidLanguage
import web, re, unicodedata, urllib, json
from merge import try_merge
from openlibrary.catalog.utils import mk_norm
from pprint import pprint
from collections import defaultdict
from openlibrary.catalog.utils import flip_name
from time import sleep
from openlibrary import accounts

re_normalize = re.compile('[^[:alphanum:] ]', re.U)
 
# http://stackoverflow.com/questions/517923/what-is-the-best-way-to-remove-accents-in-a-python-unicode-string
def strip_accents(s):
    if isinstance(s, str):
        return s
    assert isinstance(s, unicode)
    return ''.join((c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn'))
 
def normalize(s): # strip non-alphanums and truncate at 25 chars
    norm = strip_accents(s).lower()
    norm = norm.replace(' and ', ' ')
    if norm.startswith('the '):
        norm = norm[4:]
    elif norm.startswith('a '):
        norm = norm[2:]
    return norm.replace(' ', '')[:25]

type_map = {
    'description': 'text',
    'notes': 'text',
    'number_of_pages': 'int',
}

class RequiredField(Exception):
    def __init__(self, f):
        self.f = f
    def __str__(self):
        return "missing required field: '%s'" % self.f

# don't use any of these as work titles
bad_titles = set(('Publications', 'Works. English', 'Missal', 'Works', 'Report', \
    'Letters', 'Calendar', 'Bulletin', 'Plays', 'Sermons', 'Correspondence', \
    'Bill', 'Bills', 'Selections', 'Selected works', 'Selected works. English', \
    'The Novels', 'Laws, etc'))

subject_fields = ['subjects', 'subject_places', 'subject_times', 'subject_people' ] 

def get_title(e):
    if not e.get('work_titles'):
        return e['title']
    wt = e['work_titles'][0]
    return e['title'] if wt in bad_titles else e['title']

def find_matching_work(e):
    """
    Input: An edition
    
    For each author, 
      find works with the author
      for each of these works,
        If it has a title and it it equal to the title of the work, return it

    Basically, returns a work for the provided edition by searching via. author
    
    """
    norm_title = mk_norm(get_title(e))

    seen = set()
    for a in e['authors']:
        q = {
            'type':'/type/work',
            'authors' : {'author' : {'key' : a['key']}}
        }
        work_keys = list(web.ctx.site.things(q))
        for wkey in work_keys:
            w = web.ctx.site.get(wkey)
            if wkey in seen:
                continue
            seen.add(wkey)
            if not w.get('title'):
                continue
            if mk_norm(w['title']) == norm_title:
                assert w.type.key == '/type/work'
                return wkey

def build_author_reply(author_in, edits):
    """
    Input: author_in is a list of authors
           edits is a return value used to indicate changed authors

    For each author in author_in
      If there's no "key" in the author, it's new
           Add it to the database and append it to edits
      add {key:authorkey} to list authors
      add {key:authorkey, name:authorname, status:created|modified} to list author_reply
    return above two

    Basically, for all authors provided, create them if they don't
    exist and return the ones along with whether they were created or
    not
           
    """
    authors = []
    author_reply = []
    for a in author_in:
        new_author = 'key' not in a
        if new_author:
            a['key'] = web.ctx.site.new_key('/type/author')
            edits.append(a)
        authors.append({'key': a['key']})
        author_reply.append({
            'key': a['key'],
            'name': a['name'],
            'status': ('created' if new_author else 'modified'),
        })
    return (authors, author_reply)

def new_work(q, rec, cover_id):
    """
    Input: rec is an edition, cover_id is cover, q is TBD.

    q is used to pick up authors from. 

    Create a new work for the given edition `rec`. Covers are attached
    if provided.
    """

    w = {
        'type': {'key': '/type/work'},
        'title': get_title(rec),
    }
    for s in subject_fields:
        if s in rec:
            w[s] = rec[s]

    if 'authors' in q:
        w['authors'] = [{'type':{'key': '/type/author_role'}, 'author': akey} for akey in q['authors']]

    wkey = web.ctx.site.new_key('/type/work')
    if cover_id:
        w['covers'] = [cover_id]
    w['key'] = wkey
    return w

def load_data(rec):
    cover_url = None
    if 'cover' in rec:
        cover_url = rec['cover']
        del rec['cover']
    try:
        q = build_query(rec)
    except InvalidLanguage as e:
        return {
            'success': False,
            'error': str(e),
        }
    edits = []

    reply = {}
    author_in = q.get('authors', [])
    (authors, author_reply) = build_author_reply(author_in, edits)

    #q['source_records'] = [loc]
    if authors:
        q['authors'] = authors
        reply['authors'] = author_reply

    wkey = None

    ekey = web.ctx.site.new_key('/type/edition')
    cover_id = None
    if cover_url:
        cover_id = add_cover(cover_url, ekey)
        q['covers'] = [cover_id]

    work_state = 'created'
    if 'authors' in q:
        wkey = find_matching_work(q)
    if wkey:
        w = web.ctx.site.get(wkey)
        work_state = 'matched'
        found_wkey_match = True
        need_update = False
        for k in subject_fields:
            if k not in rec:
                continue
            for s in rec[k]:
                if s not in w.get(k, []):
                    w.setdefault(k, []).append(s)
                    need_update = True
        if cover_id:
            w.setdefault('covers', []).append(cover_id)
            need_update = True
        if need_update:
            work_state = 'modified'
            w_dict = w.dict()
            assert w_dict and isinstance(w_dict, dict)
            edits.append(w_dict)
    else:
        w = new_work(q, rec, cover_id)
        wkey = w['key']
        edits.append(w)

    assert wkey
    q['works'] = [{'key': wkey}]
    q['key'] = ekey
    assert isinstance(q, dict)
    edits.append(q)

    assert edits
    web.ctx.site.save_many(edits, 'import new book')

    reply['success'] = True
    reply['edition'] = { 'key': ekey, 'status': 'created', }
    reply['work'] = { 'key': wkey, 'status': work_state, }
    return reply

def is_redirect(i):
    """
    Returns true if `i` is a redirect object.
    """
    if not i:
        return False
    return i.type.key == '/type/redirect'

def find_match(e1, edition_pool):
    """
    A dictionary of editions (built by build_pool)
    e1 returned by build_marc

    """
    seen = set()
    for k, v in edition_pool.iteritems():
        for edition_key in v:
            if edition_key in seen:
                continue
            thing = None
            found = True
            while not thing or is_redirect(thing):
                seen.add(edition_key)
                thing = web.ctx.site.get(edition_key)
                if thing is None:
                    found = False
                    break
                if is_redirect(thing):
                    print 'following redirect %s => %s' % (edition_key, thing['location'])
                    edition_key = thing['location']
            if not found:
                continue
            if try_merge(e1, edition_key, thing):
                return edition_key

def find_editions(rec):
    """
    Finds editions for the provided record.
    
    Returns
    {
     "title" : ["/books/OL1M", "/books/OL2M" ...]
     "ocaid" : ["/books/OL3M"...]
    }

    The functions `find_by_x` find matches on field x if it exists in record.
    
    """
    # The following functions find works which match on the given
    # fields.
    def find_by_title(title):
        query = {
            'type':'/type/edition',
            "title": title,
            }
        ekeys = list(web.ctx.site.things(query))
        return ekeys

    def find_by_isbn(isbns):
        ret = []
        for isbn in isbns:
            if len(isbn) == 10:
                query = {
                    'type':'/type/edition',
                    "isbn_10": isbn,
                    }
            if len(isbn) == 13:
                query = {
                    'type':'/type/edition',
                    "isbn_13": isbn,
                    }
            ekeys = list(web.ctx.site.things(query))
            ret.extend(ekeys)
        return ret

    matchers = dict(title   = find_by_title,
                    isbn_10 = find_by_isbn,
                    isbn_13 = find_by_isbn,
                    )
    fields = ["isbn_10", "isbn_13", "title"] 

    for field in fields:
        if field in rec:
            matcher = matchers[field]
            matches = matcher(rec[field])
            if matches:
                return {field : matches}
    
    
    

def build_pool(rec):
    """
    Creates a pool of editions that match the provided one. 
    Match criteria are. The return value will be a dict type {"criterion": matches} for each of the below.
     1. Same normalised title.
     2. Same actual title
     3. Matching ISBNs (10 and 13)
     4. matching oclc_numbers 
     5. Matching lccn

    """
    pool = defaultdict(set)
    
    ## Find records with matching title
    # First search with normalised title
    assert isinstance(rec.get('title'), basestring)
    q = {
        'type': '/type/edition',
        'normalized_title_': normalize(rec['title'])
    }
    norm_title_matches = web.ctx.site.things(q)
    if norm_title_matches: 
        pool['title'] = set(norm_title_matches)

    # Now with the actual title
    q['title'] = rec['title']
    del q['normalized_title_']
    actual_title_matches = web.ctx.site.things(q)
    if actual_title_matches:
        pool['title'].update(set(actual_title_matches))

    
    ## Find records with matching ISBNs
    isbns = rec.get('isbn', []) + rec.get('isbn_10', []) + rec.get('isbn_13', [])
    isbns = [isbn.replace("-", "").strip() for isbn in isbns] # strip hyphens
    if isbns:
        # Make a single request to find records matching the given ISBNs
        keys = web.ctx.site.things({"isbn_": isbns, 'type': '/type/edition'})
        if keys:
            pool['isbn'] = set(keys)
    
    ## Find records with matching oclc_numbers and lccn
    for field in 'oclc_numbers', 'lccn':
        values = rec.get(field, [])
        if values:
            for v in values:
                q = {field: v, 'type': '/type/edition'}
                found = web.ctx.site.things(q)
                if found:
                    pool[field] = set(found)

    retval = dict((k, list(v)) for k, v in pool.iteritems())
    return retval

def add_db_name(rec):
    if 'authors' not in rec:
        return

    for a in rec['authors']:
        date = None
        if 'date' in a:
            assert 'birth_date' not in a and 'death_date' not in a
            date = a['date']
        elif 'birth_date' in a or 'death_date' in a:
            date = a.get('birth_date', '') + '-' + a.get('death_date', '')
        a['db_name'] = ' '.join([a['name'], date]) if date else a['name']

re_lang = re.compile('^/languages/([a-z]{3})$')

def early_exit(rec):
    """
    Searches for matches based on ocaid, source_records, isbn_10, isbn_13 and oclc_numbers
    Returns list of keys matched
    """
    fields = ['ocaid', 'source_records', 'isbn_10', 'isbn_13', 'oclc_numbers']
    for f in fields:
        if f in rec:
            val = rec[f]
            if isinstance(val, list): # Dirty hack to use only first element in case of list values
                val = val[0]
            query = {
                'type':'/type/edition',
                f: val,
                }
            ekeys = list(web.ctx.site.things(query))
            if ekeys:
                return ekeys[0]
    return False

def find_exact_match(rec, edition_pool):
    """
    Tries to find something that exactly matches the given `rec` in
    `edition_pool`. Exact match means having the same language and
    same authors.

    rec is the input to the main `load` method. The record we want to
    add in the appropriate format
    
    edition_pool is the dictionary of matches created by build_pool. It is a dictionary of matches of the form
    {
    "title" : set(["/books/OL1M", ...]),
    "isbn"  : set(["/books/OL2M', "/books/OL3M", ...])
    .
    .
    .
    }
    Each key is the item matches upon and the value is a the set of matched items.
    """
    seen = set()
    # Do the search for every item matches were founded on (title, isbn etc.)
    for field, editions in edition_pool.iteritems():
        ## Check each match but only once. 
        for ekey in editions:
            if ekey in seen:
                continue
            seen.add(ekey)
            ## Get the existing entry with this key
            existing = web.ctx.site.get(ekey)
            match = True
            ## Take each key in the record to be added and compare
            ## against `existing`.n
            for k, v in rec.iteritems():
                print " Checking by %s"%k
                ### Don't match by source_record
                if k == 'source_records':
                    continue
                existing_value = existing.get(k)
                ### Ignore records that aren't there. 
                if not existing_value:
                    continue
                if k == 'languages':
                     existing_value = [str(re_lang.match(l.key).group(1)) for l in existing_value]
                if k == 'authors':
                     existing_value = [dict(a) for a in existing_value]
                     for a in existing_value:
                         del a['type']
                         del a['key']
                     for a in v:
                        if 'entity_type' in a:
                            del a['entity_type']
                        if 'db_name' in a:
                            del a['db_name']
                        #for f in 'name', 'personal_name':
                        #    if a.get(f):
                        #        a[f] = flip_name(a[f])

                if existing_value != v:
                    match = False
                    break
            if match:
                return ekey
    return False

def add_cover(cover_url, ekey):
    olid = ekey.split("/")[-1]
    coverstore_url = config.get('coverstore_url').rstrip('/')
    upload_url = coverstore_url + '/b/upload2' 
    user = accounts.get_current_user()
    params = {
        'author': user.key,
        'data': None,
        'source_url': cover_url,
        'olid': olid,
        'ip': web.ctx.ip,
    }
    for attempt in range(10):
        try:
            res = urllib.urlopen(upload_url, urllib.urlencode(params))
        except IOError:
            print 'retry, attempt', attempt
            sleep(2)
            continue
        body = res.read()
        if body != '':
            reply = json.loads(body)
        if res.getcode() == 200 and body != '':
            if 'id' in reply:
                break
        print 'retry, attempt', attempt
        sleep(2)
    if not reply or reply.get('message') == 'Invalid URL':
        return
    cover_id = int(reply['id'])
    return cover_id

def load(rec):
    """
    The main entry point to the import api
    
    The format of `rec` is designed just for this API. It can be built using
    openlibrary.plugins.importapi.import_edition_builder.import_edition_builder

    This function will create a record based on `rec`. 

    """
    if not rec.get('title'):
        raise RequiredField('title')
    if not rec.get('source_records'):
        raise RequiredField('source_records')
    if isinstance(rec['source_records'], basestring):
        rec['source_records'] = [rec['source_records']]
   
    edition_pool = build_pool(rec)
    if not edition_pool:
        return load_data(rec) # 'no books in pool, loading'

    #matches = set(item for sublist in edition_pool.values() for item in sublist)
    #if len(matches) == 1:
    #    return {'success': True, 'edition': {'key': list(matches)[0]}}

    match = early_exit(rec)
    if not match:
        match = find_exact_match(rec, edition_pool)

    if not match:
        rec['full_title'] = rec['title']
        if rec.get('subtitle'):
            rec['full_title'] += ' ' + rec['subtitle']
        e1 = build_marc(rec)
        add_db_name(e1)

        match = find_match(e1, edition_pool)

    if not match: # 'match found:', match, rec['ia']
        return load_data(rec)

    need_work_save = False
    need_edition_save = False
    w = None
    e = web.ctx.site.get(match)
    if e.works:
        w = e.works[0].dict()
        work_created = False
    else:
        work_created = True
        need_work_save = True
        need_edition_save = True
        w = {
            'type': {'key': '/type/work'},
            'title': get_title(rec),
            'key': web.ctx.site.new_key('/type/work'),
        }
        e.works = [{'key': w['key']}]

    reply = {
        'success': True,
        'edition': {'key': match, 'status': 'matched'},
        'work': {'key': w['key'], 'status': 'matched'},
    }

    print "e is ",e

    # Add the source we got this from to the source_records of the
    # existing edition if it's not already there.
    if not e.get('source_records'):
        e['source_records'] = []
    existing_source_records = set(e['source_records'])
    print "existing_source_records ", existing_source_records
    for i in rec['source_records']:
        if i not in existing_source_records:
            e['source_records'].append(i)
            need_edition_save = True
    assert e['source_records']
    
    
    # Now, make the edits needed (I think)
    edits = []

    
    if 'subjects' in rec:
        work_subjects = list(w.get('subjects', []))
        for s in rec['subjects']:
            if s not in work_subjects:
                work_subjects.append(s)
                need_work_save = True
        if need_work_save and work_subjects:
            w['subjects'] = work_subjects
    if 'ocaid' in rec:
        new = 'ia:' + rec['ocaid']
        if not e.ocaid:
            e['ocaid'] = rec['ocaid']
            need_edition_save = True
    if 'cover' in rec and not e.covers:
        cover_url = rec['cover']
        cover_id = add_cover(cover_url, e.key)
        if cover_id:
            e['covers'] = [cover_id]
            need_edition_save = True
            if not w.get('covers'):
                w['covers'] = [cover_id]
                need_work_save = True
    for f in 'ia_box_id', 'ia_loaded_id':
        if f not in rec:
            continue
        if e.get(f):
            assert not isinstance(e[f], basestring)
            assert isinstance(e[f], list)
            if isinstance(rec[f], basestring):
                if rec[f] not in e[f]:
                    e[f].append(rec[f])
                    need_edition_save = True
            else:
                assert isinstance(rec[f], list)
                for x in rec[f]:
                    if x not in e[f]:
                        e[f].append(x)
                        need_edition_save = True
        if isinstance(rec[f], basestring):
            e[f] = [rec[f]]
            need_edition_save = True
        else:
            assert isinstance(rec[f], list)
            e[f] = rec[f]
            need_edition_save = True
        assert not isinstance(e[f], basestring)
        assert isinstance(e[f], list)
    if need_edition_save:
        reply['edition']['status'] = 'modified'
        e_dict = e.dict()
        assert e_dict and isinstance(e_dict, dict)
        edits.append(e_dict)
    if need_work_save:
        reply['work']['status'] = 'created' if work_created else 'modified'
        edits.append(w)
    if edits:
        edits_str = `edits`
        for i in edits:
            j = `i`
            assert i
            assert isinstance(i, dict)
        web.ctx.site.save_many(edits, 'import new book')
    return reply
