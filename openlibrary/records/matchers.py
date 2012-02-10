"""
Matchers
========

This module contains a list of functions that are used to search for
records in the database.

Each function will receive a dictionary that contains the search
parameters. This shouldn't be modified (make a copy if you have to
modify it). It can do whatever kinds of searches it wants to and then
should return an iterable of keys of matched things.

The `match_functions` is a list of functions in order of running. To
reduce computation, it's a good idea to put the more accurately
matching ones which require less queries on the top (e.g. exact ISBN
searches etc.). Adding a new matcher means creating a new function and
then adding the function to this list.


"""

import copy
from collections import defaultdict
import itertools
import logging as Logging
import re


from infogami import config
from openlibrary.utils.solr import Solr
import web


logger = Logging.getLogger(__name__)

# Helper functions
def get_works_solr():
    base_url = "http://%s/solr/works" % config.plugin_worksearch.get('solr')
    return Solr(base_url)


def get_authors_solr():
    base_url = "http://%s/solr/authors" % config.plugin_worksearch.get('author_solr')
    return Solr(base_url)


def get_publish_year(pdate):
    if pdate:
        year = re.compile('([0-9]{4})')
        s = year.search(pdate)
        return s and s.groups()[0]
    else:
        return False
    
    

def match_isbn(params):
    "Search by ISBN for exact matches"
    def normalise(isbn):
        return str(isbn).strip().upper().replace(" ", "").replace("-", "")

    if "isbn" in params.get("identifiers",{}):
        isbns = params["identifiers"]["isbn"]
        q = {
            'type':'/type/edition',
            'isbn_': [normalise(x) for x in isbns]
            }
        logger.debug("ISBN query : %s", q)
        ekeys = list(web.ctx.site.things(q))
        if ekeys:
            return ekeys
    return []

def match_identifiers(params):
    "Match by identifiers"
    counts = defaultdict(int)
    identifiers = copy.deepcopy(params.get("identifiers",{}))
    for i in ["oclc_numbers", "lccn", "ocaid"]:
        if i in identifiers:
            val = identifiers.pop(i)
            query = {'type':'/type/edition',
                     i : val}
            matches = web.ctx.site.things(query)
            for i in matches:
                counts[i] += 1
    for k,v in identifiers.iteritems(): # Rest of the identifiers
        query = {'type':'/type/edition',
                 'identifiers' : {k : v}}
        matches = web.ctx.site.things(query)
        for i in matches:
            counts[i] += 1

    return sorted(counts, key = counts.__getitem__, reverse = True)


def match_tap_solr(params):
    """Search solr for works using title and author and narrow using
    publishers.

    Note:
    This function is ugly and the idea is to contain ugliness here
    itself so that it doesn't leak into the rest of the library.
    
    """
    wsolr = get_works_solr()
    # Find matching works
    query = {}
    works = []
    if "title" in params:
        query['title'] = params['title']

    if "authors" in params:
        authors = [x['name'] for x in params['authors']]
        query['author_name'] = authors
    
    if query:
        work_results = wsolr.select(query, q_op="AND")['docs']
    

    work_keys = [x.key for x in work_results]
    works = list(web.ctx.site.get("/works/%s"%y) for y in work_keys)
    edition_keys = list(itertools.chain(*[x.edition_key for x in work_results if x.edition_count]))
    editions = list(web.ctx.site.get("/books/%s"%y) for y in edition_keys)

    if editions:
        matches = editions
    else:
        matches = works

    # Now filter the editions by publisher and publish_date if provided
    publisher = params.get('publisher',"")
    publisher = publisher and publisher.split()[0].lower()

    publish_year = params.get('publish_date')
    publish_year = get_publish_year(publish_year)
    
    if publisher:
        matches = itertools.ifilter(lambda x: x.get('publisher',"").split()[0].lower() == publisher,
                                    matches)
    if publish_year:
        matches = itertools.ifilter(lambda x: get_publish_year(x.get('publish_date','')) == publish_year,
                                    matches)
    return [x.key for x in matches]


match_functions = [match_isbn,
                   match_identifiers,
                   match_tap_solr
                   ]



