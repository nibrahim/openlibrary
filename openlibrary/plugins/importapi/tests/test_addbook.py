import py.test
from .. import addbook

def test_search_bad_args():
    "Tries calling addbook.search without any arguments"
    py.test.raises(TypeError, addbook.search)
