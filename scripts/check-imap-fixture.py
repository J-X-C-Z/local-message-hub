#!/usr/bin/env python3
"""Real local TLS transport against one explicit synthetic mailbox; no credentials."""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import socket
import sqlite3
import ssl
import subprocess
import tempfile
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from integrations.email import adapter

spec = importlib.util.spec_from_file_location(
    "runtime_smoke",Path(__file__).with_name("smoke-linux-runtime.py"))
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
BIN = runtime.BIN
GOOD_HOST = "hub-imap-fixture.localhost"
ACCOUNT = "mailbox@example.invalid"
PEER = "peer@example.invalid"
EXAMINE = b"a1 EXAMINE INBOX\r\n"


def certificates(folder):
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"Synthetic fixture authority")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
          .sign(ca_key,hashes.SHA256()))
    ca_path = folder/"ca.der"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.DER))

    def leaf(host, name):
        key=ec.generate_private_key(ec.SECP256R1())
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,host)])
        certificate=(x509.CertificateBuilder().subject_name(subject).issuer_name(ca_name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]),critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
            .sign(ca_key,hashes.SHA256()))
        key_path=folder/(name+".key")
        key_path.touch(mode=0o600)
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        cert_path=folder/(name+".pem")
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version=ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(cert_path,key_path)
        return context,certificate.fingerprint(hashes.SHA256()).hex()

    return ca_path,leaf(GOOD_HOST,"good"),leaf("wrong-fixture.localhost","wrong")


def fetch_line(uid):
    return f"a2 UID FETCH {uid} (UID BODY.PEEK[])\r\n".encode()


def mail(message_id, text):
    return (f"From: Peer <{PEER}>\r\nTo: {ACCOUNT}\r\n"
        f"Date: Sat, 25 Oct 2025 04:02:01 -0500\r\nSubject: Synthetic test\r\n"
        f"Message-ID: <{message_id}@example.invalid>\r\n\r\n{text}\r\n").encode()


class Fixture:
    def __init__(self, context, body, uid=5, validity=77, profile=ACCOUNT, malformed=None):
        self.socket = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.socket.settimeout(5)
        self.socket.bind(("127.0.0.1",0))
        self.socket.listen(1)
        self.port=self.socket.getsockname()[1]
        self.wire=[]
        self.error=None
        def serve():
            try:
                with self.socket:
                    conn,_=self.socket.accept()
                    with conn:
                        conn.settimeout(5)
                        with context.wrap_socket(conn,server_side=True) as tls:
                            assert tls.version()=="TLSv1.3"
                            self.serve(tls,body,uid,validity,profile,malformed)
            except (OSError,ssl.SSLError):
                # Failed CA/name/pin and closed reads produce no accepted command.
                pass
            except BaseException as exc:
                self.error=exc
        self.thread=threading.Thread(target=serve,daemon=True)
        self.thread.start()

    def serve(self, tls, body, uid, validity, profile, malformed):
        marker=hashlib.sha256(b"imap-fixture-mailbox-v1\0"+profile.encode()).hexdigest()
        tls.sendall(f"* PREAUTH [CAPABILITY IMAP4rev1] local-message-hub synthetic fixture {marker}\r\n".encode())
        with tls.makefile("rb") as reader:
            command=reader.readline(513)
            if not command: return
            self.wire.append(command)
            assert command==EXAMINE
            tls.sendall((f"* FLAGS (\\Seen)\r\n* 1 EXISTS\r\n* OK [UIDVALIDITY {validity}] fixture epoch\r\n"
                         "a1 OK [READ-ONLY] EXAMINE complete\r\n").encode())
            command=reader.readline(513)
            if not command: return
            self.wire.append(command)
            assert command==fetch_line(uid)
            if malformed=="uid":
                tls.sendall(f"* 1 FETCH (UID {uid+1} BODY[] {{2}}\r\n".encode())
            elif malformed=="oversized":
                tls.sendall(f"* 1 FETCH (UID {uid} BODY[] {{262145}}\r\n".encode())
            else:
                tls.sendall(f"* 1 FETCH (UID {uid} BODY[] {{{len(body)}}}\r\n".encode()+
                            body+b")\r\na2 OK FETCH complete\r\n")
            # Client must close; no commands beyond EXAMINE and the explicit UID FETCH.
            assert not reader.readline(513)

    def finish(self, expect):
        self.thread.join(6)
        assert not self.thread.is_alive()
        if self.error:
            raise self.error
        assert self.wire==expect,self.wire


def check(root):
    folder=root/"native"
    folder.mkdir(mode=0o700)
    given=subprocess.run([BIN,"init-live","--directory",folder],capture_output=True,timeout=7,check=True)
    assert given.returncode==0
    db_path=folder/"native-v1.sqlite3"
    ca,(context,leaf),(wrong,wrong_pin)=certificates(root)
    alternate=root/"alternate"
    alternate.mkdir(mode=0o700)
    wrong_ca,_,_=certificates(alternate)
    first=mail("first","synthetic text, no attachment")
    second=mail("second","offline second record")

    def read(sql):
        db=sqlite3.connect(db_path.as_uri()+"?mode=ro",uri=True)
        try:
            return db.execute(sql).fetchall()
        finally:
            db.close()

    def sql(command):
        db=sqlite3.connect(db_path)
        try:
            db.executescript(command)
            db.commit()
        finally:
            db.close()

    def call(command="preview-imap-fixture", after=0, uid=5, validity=77, body=first,
             peer_epoch=None, expected=None, failing=None, confirmation=None,
             ctx=context, pin=leaf, profile=ACCOUNT, account=ACCOUNT, malformed=None, ca_file=ca):
        wire=Fixture(ctx,body,uid,validity if peer_epoch is None else peer_epoch,profile,malformed)
        argv=[BIN,command,"--directory",folder,"--port",str(wire.port),"--ca-der",ca_file,
            "--leaf-sha256",pin,"--account",account,"--uidvalidity",str(validity),
            "--after",str(after),"--uid",str(uid)]
        if confirmation:
            argv.extend(["--confirm",confirmation])
        try:
            result=subprocess.run(argv,capture_output=True,timeout=7)
        finally:
            # A pre-TLS stale preview refusal may leave the listener unused.
            if expected==[] and wire.thread.is_alive():
                wire.socket.close()
            wire.finish([EXAMINE,fetch_line(uid)] if expected is None else expected)
        if failing is not None:
            assert result.returncode!=0 and not result.stdout and failing in result.stderr.decode(),result
            assert ACCOUNT.encode() not in result.stderr
            return None
        assert result.returncode==0,result.stderr.decode()
        return json.loads(result.stdout)

    call(pin="0"*64,expected=[],failing="certificate verification failed")
    call(ca_file=wrong_ca,expected=[],failing="certificate verification failed")
    call(ctx=wrong,pin=wrong_pin,expected=[],failing="certificate verification failed")
    call(profile=PEER,expected=[],failing="outside the readonly protocol")
    call(peer_epoch=78,expected=[EXAMINE],failing="UIDVALIDITY changed")
    call(malformed="uid",failing="outside the readonly protocol")
    call(malformed="oversized",failing="outside the readonly protocol")
    assert not read("SELECT name FROM sqlite_schema WHERE name='native_imap_fixture_progress'")
    assert not read("SELECT id FROM messages")
    begin=call()
    assert begin["uidvalidity"]==77 and begin["previous_uid"]==0 and begin["uid"]==5
    digest=begin["confirmation"]
    assert not read("SELECT name FROM sqlite_schema WHERE name='native_imap_fixture_progress'")
    call("import-imap-fixture",body=second,confirmation=digest,failing="confirmation does not match")
    assert not read("SELECT id FROM messages")

    commit=call("import-imap-fixture",confirmation=digest)
    assert commit["created"]==1 and commit["uid"]==5
    assert commit==call("import-imap-fixture",confirmation=digest)
    assert read("SELECT uidvalidity,uid FROM native_imap_fixture_progress")==[(77,5)]
    old=adapter.parse_email_export(first,"eml",ACCOUNT)["messages"][0]
    assert read("SELECT id,conversation_id,timestamp,sender_id,text FROM messages")==[
        tuple(old[k] for k in ("id","conversation_id","timestamp","sender_id"))+
        (old["text"].split("\n\n",1)[1],)]
    call(uid=9,after=0,expected=[],failing="previous UID")
    call(uid=9,after=5,validity=78,expected=[],failing="UIDVALIDITY changed")
    call(uid=9,after=5,peer_epoch=78,expected=[EXAMINE],failing="UIDVALIDITY changed")

    next_window=call(uid=9,after=5,body=second)
    # Late SQLite fault: the message write has executed, and both the
    # source cursor and that row must still rollback as one unit.
    sql("CREATE TRIGGER stop_progress BEFORE UPDATE ON native_imap_fixture_progress "
        "BEGIN SELECT RAISE(ABORT,'synthetic'); END;")
    call("import-imap-fixture",uid=9,after=5,body=second,confirmation=next_window["confirmation"],
         failing="fixture commit failed")
    assert read("SELECT count(*) FROM messages")==[(1,)]
    assert read("SELECT uid FROM native_imap_fixture_progress")==[(5,)]
    sql("DROP TRIGGER stop_progress")
    assert call("import-imap-fixture",uid=9,after=5,body=second,
               confirmation=next_window["confirmation"])["created"]==1
    assert read("SELECT uid FROM native_imap_fixture_progress")==[(9,)]
    assert commit==call("import-imap-fixture",confirmation=digest)
    assert read("SELECT uid FROM native_imap_fixture_progress")==[(9,)]
    # Same certificate and UID, different explicitly bound profile (same
    # fictional mail belongs to both recipients/participants).
    other=call(account=PEER,profile=PEER)
    assert other["confirmation"]!=digest
    assert call("import-imap-fixture",account=PEER,profile=PEER,
                confirmation=other["confirmation"])["created"]==1
    assert read("SELECT uid FROM native_imap_fixture_progress ORDER BY uid")==[(5,),(9,)]
    assert read("SELECT count(*) FROM native_event_journal")==[(0,)]
    assert read("SELECT count(*) FROM messages")==[(3,)]


if __name__=="__main__":
    with tempfile.TemporaryDirectory(prefix="lmh-imap-fixture-") as path:
        check(Path(path))
    print("extracted Rust TLS1.3 synthetic IMAP: exact host/leaf/account binding, only EXAMINE and UID BODY.PEEK, blocked epoch/UID/oversize, atomic rollback, stable retry/restart, old shared EML fields")
