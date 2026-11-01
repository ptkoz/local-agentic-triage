import fcntl
import os

class Busy(RuntimeError): pass

class Lock:
    def __init__(self,path,blocking=False): self.path=path; self.blocking=blocking; self.fd=None
    def __enter__(self):
        self.fd=os.open(self.path,os.O_CREAT|os.O_RDWR,0o600)
        operation=fcntl.LOCK_EX if self.blocking else fcntl.LOCK_EX|fcntl.LOCK_NB
        try: fcntl.flock(self.fd,operation)
        except BlockingIOError:
            os.close(self.fd); self.fd=None; raise Busy('lock busy') from None
        return self
    def __exit__(self,*args):
        if self.fd is not None:
            os.close(self.fd); self.fd=None
