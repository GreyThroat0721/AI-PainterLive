import jieba.posseg as _p
from jieba.posseg import *
for _n in dir(_p):
    if not _n.startswith("_"):
        globals()[_n] = getattr(_p, _n)
