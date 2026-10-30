# gradio 空壳：让 inference_webui 的顶层 import 通过，不提供 UI
import sys as _sys, types as _t
class _Any:
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return _Any()
    def __getattr__(self, n): return _Any()
    def __iter__(self): return iter(())
    def __enter__(self): return self
    def __exit__(self, *a): return False
class _M(_t.ModuleType):
    def __getattr__(self, n):
        if n.startswith("__"): raise AttributeError(n)
        return _Any
_sys.modules[__name__] = _M(__name__)
