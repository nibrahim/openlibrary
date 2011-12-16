"""
The primary library used to add books to Open Library. 

This will be called from any part of the system that wishes to update
the database.

"""

def search(**kwargs):
    """
    Finds records matching given data. 

    Search fields are as follows. Atleast one should be specified.

    1. isbns - List of ISBN13 or ISBN10 numbers. Each is a string.
    2. title - Title of the edition. A string.
    3. authors - List of authors. Each is a dictionary of the form. 
                 {
                  "name" : "Name of the author",
                  "dob"  : "Date of birth",
                  "dod"  : "Date of death"
                 }
                 The dob and dod fields are optional

    4. publisher - Name of publisher. A string.
    5. publish_year - Year of publishing. 
    6. lccn - List of Library of Congress Catalogue numbers. Each is a string.
    
    Returns:

    { "matches" : { "editions" : 
                    [ { "score"   : 1.0,             # Confidence in match
                        "matches" : ["/book/OL1M",], # List of matched items
                        },

                      { "score"   : 0.9,          
                        "matches" : ["/book/OL1M",],
                        },

                      { "score"   : 0.8,          
                        "matches" : ["/book/OL1M","/book/OL2M"],
                        },
                      ],

                    "works": 
                    [ { "score"   : 1.0,
                        "matches" : ["/work/OL1W",]
                        },
                      ]
                      }
    """
    if not kwargs:
        raise TypeError("Atleast one parameter should be specified")
