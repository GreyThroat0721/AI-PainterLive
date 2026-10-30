# opencc 空实现：简体直播场景不需要繁简转换
class OpenCC:
    def __init__(self, config=""):
        self.config = config
    def convert(self, text):
        return text
    def __call__(self, text):
        return text

def convert(text, config=""):
    return text
