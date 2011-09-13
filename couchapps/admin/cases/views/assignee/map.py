def map(doc):
    if doc.get("type","") == "case":
        yield [doc.get('assignee'), doc.get('status'), doc.get('history',[{}])[-1].get("at","") ], 1
        yield [None, doc.get('status'), doc.get('history',[{}])[-1].get("at","") ], 1
