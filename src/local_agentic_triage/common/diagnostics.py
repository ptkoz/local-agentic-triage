import re


class CommandError(RuntimeError):
    def __init__(self,code,returncode=None):
        super().__init__(code)
        self.code=code
        self.returncode=returncode if type(returncode) is int and -(2**31)<=returncode<2**31 else None


def error_code(exc):
    if isinstance(exc,CommandError): return exc.code
    text=type(exc).__name__
    text=text.replace('JSONDecodeError','json_decode_error').replace('OSError','os_error')
    return re.sub(r'(?<!^)(?=[A-Z])','_',text).lower()


def error_suffix(exc):
    value=getattr(exc,'returncode',None)
    return f' returncode={value}' if type(value) is int and -(2**31)<=value<2**31 else ''
