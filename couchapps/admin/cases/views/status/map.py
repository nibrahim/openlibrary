def map(doc):
    status = doc.get("status","")
    if status == "new" or status == "replied":
        yield ["open", doc.get('history',[{}])[-1].get("at","")], None
    if status == "closed":
        yield ["closed", doc.get('history',[{}])[-1].get("at","")], None
        
