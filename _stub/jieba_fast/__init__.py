import jieba
import jieba.analyse
from jieba import *
lcut = jieba.lcut
cut = jieba.cut
lcut_for_search = jieba.lcut_for_search
tokenize = jieba.tokenize
analyse = jieba.analyse
initialize = getattr(jieba, "initialize", lambda *a, **k: None)
setLogLevel = jieba.setLogLevel
from . import posseg
