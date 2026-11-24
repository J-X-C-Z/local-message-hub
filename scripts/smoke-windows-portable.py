#!/usr/bin/env python3
"""Future Windows x64 synthetic smoke: verified foreground package and managed owner pipes."""
from argparse import ArgumentParser
from contextlib import contextmanager, closing
import ctypes as ct
from ctypes import wintypes as w
from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import re
import secrets
import socket
import stat
import subprocess as sp
import sys
import tempfile
from threading import Thread
from time import monotonic, sleep
from urllib.parse import urlencode

START = 18
STOP = 12
SCOPE = "synthetic-owner"
FOREIGN = "synthetic-foreign"
READ = "/api/v1/messages?"
SYNTHETIC = "synthetic owned marker"
OTHER = "synthetic foreign marker"
GATE = '''param([string]$Wrapper,[string]$Archive,[string]$Grants)
$ErrorActionPreference='Stop'
if ([Console]::In.ReadLine() -cne 'owned-test-job') { exit 129 }
& $Wrapper -Mode Live -Archive $Archive -Grants $Grants -Port 0
exit $LASTEXITCODE
'''
ACL = r'''
$ErrorActionPreference='Stop'
$acl=Get-Acl -LiteralPath $env:LMH_TEST_ACL_PATH
$me=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
$allow=@($me,'S-1-5-18','S-1-5-32-544')
$unexpected=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) |
  Where-Object { $_.AccessControlType -eq 'Allow' -and $_.IdentityReference.Value -notin $allow })
if ($owner -ne $me -or $unexpected.Count -ne 0) { exit 131 }
'''


def demand(test, description):
    if not test:
        raise RuntimeError(description)


def api(name, result, *parameters):
    fn = getattr(ct.WinDLL("kernel32", use_last_error=True), name)
    fn.restype = result
    fn.argtypes = parameters
    return fn


def native_windows():
    demand(sys.platform == "win32", "run only on a native Windows x64 test machine")
    demand(sys.implementation.name == "cpython" and sys.version_info >= (3, 12),
           "requires native CPython 3.12 or newer")
    current = api("GetCurrentProcess", w.HANDLE)()
    query = api("IsWow64Process2", w.BOOL, w.HANDLE, ct.POINTER(w.USHORT), ct.POINTER(w.USHORT))
    process, native = w.USHORT(), w.USHORT()
    demand(query(current, ct.byref(process), ct.byref(native)) and
           native.value == 0x8664 and process.value == 0 and ct.sizeof(ct.c_void_p) == 8,
           "requires native Windows x64 and native x64 CPython")


def local_path(value, directory=False):
    path = Path(value)
    demand(re.fullmatch(r"[A-Za-z]:", path.drive) and path.is_absolute() and
           len(path.parts) > 1, "use an absolute ordinary drive-letter path")
    demand(api("GetDriveTypeW", w.UINT, w.LPCWSTR)(path.anchor) == 3, "requires a fixed volume")
    filesystem=ct.create_unicode_buffer(32)
    volume=api("GetVolumeInformationW",w.BOOL,w.LPCWSTR,w.LPWSTR,w.DWORD,
               ct.POINTER(w.DWORD),ct.POINTER(w.DWORD),ct.POINTER(w.DWORD),w.LPWSTR,w.DWORD)
    demand(volume(path.anchor,None,0,None,None,None,filesystem,len(filesystem))
           and filesystem.value=="NTFS","requires a local fixed NTFS volume")
    cursor = Path(path.anchor)
    for i, name in enumerate(path.parts[1:], 1):
        demand(name not in (".", "..") and not name.endswith((" ", ".")) and
               not any(ord(x) < 32 or x == ":" for x in name), "unsupported path spelling")
        cursor /= name
        info = cursor.lstat()
        expect_directory = i < len(path.parts)-1 or directory
        demand(not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT and
               (stat.S_ISDIR(info.st_mode) if expect_directory else stat.S_ISREG(info.st_mode)),
               "unexpected type or reparse point in supplied path")
    return path


def file_hash(path, max_size=256*1024*1024):
    path = local_path(path)
    info = path.stat()
    demand(0 < info.st_size <= max_size and info.st_nlink == 1, "unexpected artifact size or links")
    with path.open("rb") as file:
        digest = sha256()
        count = 0
        while chunk := file.read(65536):
            count += len(chunk)
            demand(count <= max_size, "artifact grew while hashing")
            digest.update(chunk)
    demand(count == info.st_size, "artifact changed while hashing")
    return digest.hexdigest()


def powershell(pwsh, *args):
    return [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", *args]


def private_acl(pwsh, path):
    # Conservative validation of the supplied leaf's owner/DACL, not an
    # effective-rights audit of ancestors and never a permissions mutation.
    result = sp.run(powershell(pwsh, "-Command", ACL), capture_output=True, timeout=15,
                    env={**os.environ, "LMH_TEST_ACL_PATH":str(path)})
    demand(result.returncode == 0, "expected current owner and only user/System/Administrators allow ACEs")


def verify(args):
    package = local_path(args.package, directory=True)
    exe = local_path(package/"bin"/"local-message-hub-rs.exe")
    wrapper = local_path(package/"run-core.ps1")
    verifier = local_path(args.verifier)
    pwsh = local_path(args.pwsh)
    demand(file_hash(verifier)==args.verifier_sha256, "trusted verifier digest mismatch")
    demand(file_hash(wrapper, 128*1024)==args.wrapper_sha256, "foreground launcher digest mismatch")
    demand(file_hash(exe)==args.binary_sha256, "candidate digest mismatch")
    for path in (package, exe, wrapper, verifier):
        private_acl(pwsh, path)
    result = sp.run([verifier,"verify-portable-core",package,args.manifest_sha256],
                    check=True,capture_output=True,timeout=25)
    demand(json.loads(result.stdout) == {"target":"windows-x64","source_revision":args.source_revision,
        "manifest_sha256":args.manifest_sha256,"binary_sha256":args.binary_sha256,"files":3},
        "package verification did not match the independent exact pins")
    return pwsh, exe, wrapper


class Accounting(ct.Structure):
    _fields_ = [("TotalUserTime",ct.c_longlong),("TotalKernelTime",ct.c_longlong),
                ("ThisPeriodUserTime",ct.c_longlong),("ThisPeriodKernelTime",ct.c_longlong),
                ("TotalPageFaultCount",w.DWORD),("TotalProcesses",w.DWORD),
                ("ActiveProcesses",w.DWORD),("TotalTerminatedProcesses",w.DWORD)]


class ForegroundJob:
    """Test-only containment for a gated wrapper, not the product supervisor."""
    def __enter__(self):
        self.handle = api("CreateJobObjectW", w.HANDLE, ct.c_void_p, w.LPCWSTR)(None, None)
        demand(self.handle, "could not create a private test-only Job Object")
        self.stopped = False
        return self

    def assign(self, child):
        opened = api("OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)(0x101,False,child.pid)
        demand(opened, "could not obtain the newly launched wrapper process handle")
        try:
            assign = api("AssignProcessToJobObject", w.BOOL, w.HANDLE, w.HANDLE)
            demand(assign(self.handle,opened), "runner does not support nested test job")
        finally:
            api("CloseHandle", w.BOOL, w.HANDLE)(opened)

    def stop(self):
        if self.stopped:
            return
        kill = api("TerminateJobObject", w.BOOL, w.HANDLE, w.UINT)
        demand(kill(self.handle,130), "test foreground Job could not be terminated")
        status = Accounting()
        query = api("QueryInformationJobObject", w.BOOL, w.HANDLE, ct.c_int,
                    ct.c_void_p, w.DWORD, ct.POINTER(w.DWORD))
        deadline = monotonic()+STOP
        while monotonic() < deadline:
            demand(query(self.handle,1,ct.byref(status),ct.sizeof(status),None),
                   "could not observe foreground test job accounting")
            if status.ActiveProcesses == 0:
                self.stopped = True
                return
            sleep(.04)
        raise RuntimeError("foreground test job did not empty")

    def __exit__(self, *_):
        try:
            self.stop()
        finally:
            api("CloseHandle", w.BOOL, w.HANDLE)(self.handle)


@contextmanager
def owned_process(command):
    child = sp.Popen(command,stdin=sp.PIPE,stdout=sp.PIPE,stderr=sp.DEVNULL,bufsize=0,close_fds=True)
    frames = Queue(maxsize=32)

    def drain():
        overflow=False
        while True:
            raw=child.stdout.readline(1025)
            if not raw:
                if overflow:
                    # Fail rather than silently skip a frame. Do not block on
                    # the observer if it has already rejected malformed output.
                    while not frames.empty():
                        try:
                            frames.get_nowait()
                        except Empty:
                            break
                    frames.put_nowait(b"")
                else:
                    try:
                        frames.put_nowait(None)
                    except Full:
                        pass
                return
            if not overflow:
                try:
                    frames.put_nowait(raw)
                except Full:
                    overflow=True

    reader=Thread(target=drain,daemon=True)
    reader.start()
    try:
        yield child, frames
    finally:
        if not child.stdin.closed:
            child.stdin.close()
        try:
            child.wait(timeout=STOP)
        except sp.TimeoutExpired:
            # Only the process this harness directly created. Managed child
            # disappearance is separately asserted, not assumed here.
            child.kill()
            child.wait(timeout=3)
        reader.join(timeout=3)
        child.stdout.close()


def frame(frames, timeout=START):
    try:
        value=frames.get(timeout=timeout)
    except Empty as failure:
        raise RuntimeError("ready/stop frame timeout") from failure
    if value is None:
        return value
    demand(value.endswith(b"\n") and len(value)<=1024, "invalid bounded control frame")
    result=json.loads(value)
    demand(isinstance(result,dict),"expected a JSON object control frame")
    return result


def ready(frames, managed):
    got=frame(frames)
    expected={"origin","event","mode","api_version"}
    if managed:
        expected.add("backend_pid")
    demand(got is not None and set(got)==expected and got["event"]=="ready" and
           got["mode"]=="native-live" and got["api_version"]==1, "unexpected readiness frame")
    port=re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})",got["origin"])
    demand(port is not None and int(port[1]) <= 65535,"expected canonical IPv4 loopback")
    return int(port[1]), got.get("backend_pid")


@contextmanager
def observed_backend(pid, parent):
    demand(type(pid)==int and pid>0 and pid not in {parent,os.getpid()},
           "invalid managed backend PID")
    # Hold a real process handle for this observed backend, not polling a PID
    # that can be recycled. This handle has SYNCHRONIZE rights only.
    handle=api("OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)(0x100000,False,pid)
    demand(handle, "could not observe managed backend lifetime")
    try:
        wait=api("WaitForSingleObject",w.DWORD,w.HANDLE,w.DWORD)
        demand(wait(handle,0)==0x102,"managed backend was not running")
        yield lambda: demand(wait(handle,1000)==0,"managed backend did not exit/reap")
    finally:
        api("CloseHandle",w.BOOL,w.HANDLE)(handle)


def http(port,path,token=None,method="GET",body=None):
    headers={"Connection":"close"}
    if token:
        headers["Authorization"]="Bearer "+token
    if body is not None:
        headers["Content-Type"]="application/json"
        body=json.dumps(body).encode()
    with closing(http.client.HTTPConnection("127.0.0.1",port,timeout=4)) as conn:
        conn.request(method,path,body,headers)
        response=conn.getresponse()
        data=response.read(16385)
        demand(len(data)<16385,"unbounded loopback response")
        return response.status,json.loads(data)


def denied(status, data, *, code="permission_required"):
    demand(status==403 and data=={"error":{"code":code}},
           "did not receive the exact expected scoped denial")


def health_and_scope(port, token, seed=None):
    demand(http(port,"/healthz")== (200,{"status":"ok"}),"unhealthy loopback")
    path=READ+urlencode({"platform":"qq","account_id":SCOPE,"conversation_id":"room"})
    demand(http(port,path)[0]==401,"missing bearer was not denied")
    demand(http(port,path,"0"*64)[0]==401,"bad bearer was not denied")
    if seed is not None:
        batch={"idempotency_key":"synthetic-write","messages":[
            {"platform":"qq","account_id":account,"conversation_id":"room","message_id":"same-id",
             "timestamp":1724000000,"text":body,"is_self":False}
            for account,body in [(SCOPE,SYNTHETIC),(FOREIGN,OTHER)]]}
        denied(*http(port,"/api/v1/ingest",token,"POST",batch))
        status, receipt=http(port,"/api/v1/ingest",seed,"POST",batch)
        demand(status==200 and receipt["created"]==2,"fixture ingest did not create both scoped rows")
    status, page=http(port,path,token)
    rows=page["items"]
    demand(status==200 and len(rows)==1 and rows[0]["id"]=="same-id" and
           rows[0]["account_id"]==SCOPE and rows[0]["text"]==SYNTHETIC, "wrong scoped result")
    denied(*http(port,path.replace(SCOPE,FOREIGN),token), code="account_scope_mismatch")


def gone(port):
    deadline=monotonic()+3
    while monotonic()<deadline:
        try:
            with socket.create_connection(("127.0.0.1",port),timeout=.2):
                pass
        except ConnectionRefusedError:
            return
        sleep(.04)
    raise RuntimeError("test endpoint still accepts TCP connections")


def run(pwsh,exe,wrapper,root,mode):
    archive=root/"native"
    archive.mkdir()
    grants=root/"grants.json"
    token, producer=secrets.token_hex(32),secrets.token_hex(32)

    def grant(ident, key, kind, permission, **extra):
        return {"id":ident,"token_sha256":sha256(key.encode()).hexdigest(),
                "kind":kind,"permissions":[permission],**extra}

    grants.write_text(json.dumps({"version":1,"grants":[
        grant("fixture.desktop",token,"desktop","messages.read",
              account_scope={"platform":"qq","account_id":SCOPE}),
        grant("fixture.producer",producer,"producer","messages.ingest"),
    ]}),encoding="utf-8")
    for path in (root, archive, grants):
        private_acl(pwsh,path)
    created=sp.run([exe,"init-live","--directory",archive],capture_output=True,check=True,timeout=15)
    demand(json.loads(created.stdout)["mode"]=="native-live","fresh archive not initialized")
    command=[exe,"desktop-supervise","--live",archive,"--grants",grants]

    if mode=="foreground":
        gate=root/"foreground-gate.ps1"
        gate.write_text(GATE,encoding="utf-8")
        # The first PowerShell process cannot invoke the product wrapper until
        # the parent assigns the test-only Job and releases this one-shot gate.
        with ForegroundJob() as job, owned_process(
                powershell(pwsh,"-File",gate,wrapper,archive,grants)) as (child,frames):
            try:
                job.assign(child)
                child.stdin.write(b"owned-test-job\n")
                child.stdin.flush()
                port,_=ready(frames,False)
                health_and_scope(port,token,producer)
            finally:
                # End the Job before joining the pipe-drain thread; a
                # descendant can still have the inherited stdout handle.
                job.stop()
            child.wait(timeout=STOP)
            gone(port)
        print("PASS foreground package: startup, liveness, scoped grant and test-job teardown (not product graceful stop)")
        return

    # Managed tests seed via their first direct supervisor; no foreground
    # wrapper or test Job Object can mask the managed owner/reap behavior.
    for case in ("stop","eof","invalid"):
        with owned_process(command) as (child,frames):
            port,pid=ready(frames,True)
            with observed_backend(pid,child.pid) as reaped:
                health_and_scope(port,token,producer if case=="stop" else None)
                if case=="eof":
                    child.stdin.close()
                else:
                    child.stdin.write(b"stop\n" if case=="stop" else b"bad\n")
                    child.stdin.flush()
                reply=frame(frames,STOP)
                result=child.wait(timeout=STOP)
                if case=="invalid":
                    demand(result!=0 and reply is None,"invalid outer control was treated as a clean stop")
                else:
                    demand(result==0 and reply=={"event":"stopped","forced":False},
                           "managed normal shutdown was not clean")
                reaped()
                gone(port)
        print("PASS managed native-live "+case+": scoped liveness, child process exit and closed endpoint")


def main():
    native_windows()
    parser=ArgumentParser(description=__doc__)
    for arg in ("pwsh","verifier","verifier-sha256","package","manifest-sha256",
                "binary-sha256","wrapper-sha256","source-revision","private-scratch"):
        parser.add_argument("--"+arg,required=True)
    parser.add_argument("--mode",required=True,choices=("foreground","managed"))
    args=parser.parse_args()
    for name in ("verifier_sha256","manifest_sha256","binary_sha256","wrapper_sha256","source_revision"):
        size=40 if name=="source_revision" else 64
        demand(re.fullmatch(r"[0-9a-f]{%d}"%size,getattr(args,name)),"invalid independent pin")
    pwsh,exe,wrapper=verify(args)
    parent=local_path(args.private_scratch,directory=True)
    private_acl(pwsh,parent)
    with tempfile.TemporaryDirectory(prefix="lmh-windows-smoke-",dir=parent) as folder:
        run(pwsh,exe,wrapper,Path(folder),args.mode)


if __name__=="__main__":
    main()
