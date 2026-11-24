#!/usr/bin/env python3
"""Package end-to-end tests for the trusted source broker (synthetic only)."""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import time

spec = importlib.util.spec_from_file_location(
    "source_runtime", Path(__file__).with_name("check-scoped-producer.py"))
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)
runtime = source.runtime
SDK = runtime.BIN.parent / "source-sdk"
BROKER = SDK / "broker.py"
SYNTHETIC = SDK / "synthetic-source.py"
STATUS = "/api/v1/source/checkpoint"


def fixture(root, name, body):
    path = root / name
    path.write_text("#!/usr/bin/python3\nimport sys,os,json,time\n" + body)
    path.chmod(0o700)
    return path


def check(root):
    archive = root/"live"
    archive.mkdir(mode=0o700)
    subprocess.run([runtime.BIN, "init-live", "--directory",archive],
                   capture_output=True,check=True,timeout=8)
    config = root/"grants.json"
    config.touch(mode=0o600)
    a, b, d = (secrets.token_hex(32) for _ in range(3))
    grants = [
       source.grant("sdk.source","scoped-producer",("qq","synthetic"),["messages.ingest"],a),
       source.grant("sdk.other","scoped-producer",("qq","foreign"),["messages.ingest"],b),
       source.grant("sdk.desktop","desktop",("qq","synthetic"),["messages.read","events.read"],d),
    ]
    config.write_text(json.dumps({"version":1,"grants":grants}))
    payload = {"token":a,"grant_id":"sdk.source","platform":"qq","account_id":"synthetic"}
    tasks = []

    def start():
        proc = runtime.start(archive,config)
        tasks.append(proc)
        return proc,runtime.ready(proc)[0]

    def command(port, exe):
        return [sys.executable,BROKER,"--origin",f"http://127.0.0.1:{port}","--plugin",exe]

    def clean_output(outputs):
        raw = b" ".join(outputs)
        for marker in (a,b,d,"hidden child input","DO_NOT_FORWARD"):
            assert marker.encode() not in raw

    def broker(port, exe=SYNTHETIC, control=payload, expected=None, command_tail=()):
        result = subprocess.run([*command(port,exe),*command_tail],
            input=(json.dumps(control)+"\n").encode(),capture_output=True,timeout=12,
            env={**os.environ,"DO_NOT_FORWARD":"broker only"})
        clean_output([result.stdout,result.stderr])
        if expected is not None:
            assert result.returncode == 1 and not result.stdout, result
            assert json.loads(result.stderr) == {"error":expected}, result
        else:
            assert result.returncode == 0 and not result.stderr, result
            return json.loads(result.stdout)

    try:
        proc, port = start()
        checkpoint = runtime.get(port,STATUS,a)
        assert checkpoint["sequence"] == 0
        blocked = root/"should-not-launch"
        write_marker = f"open({str(blocked)!r},'w').close()\n"
        no_launch = fixture(root,"deny-before-run",write_marker)
        broker(port,no_launch,payload|{"account_id":"synthetic-foreign"},expected="hub_grant_mismatch")
        broker(port,no_launch,payload|{"token":b},expected="hub_grant_mismatch")
        broker(port,no_launch,payload|{"token":d},expected="hub_grant_mismatch")
        assert not blocked.exists()
        broker(port,no_launch,expected="invalid_arguments",command_tail=["--wrong",a])
        wrong_port = subprocess.run([sys.executable,BROKER,"--origin",
            f"http://127.0.0.1:{port}/unwanted","--plugin",no_launch],
            input=b"{}\n",capture_output=True,timeout=8)
        assert json.loads(wrong_port.stderr) == {"error":"literal_loopback_origin_required"}
        regular_input = root/"not-a-pipe"
        regular_input.write_text("{}\n")
        with regular_input.open("rb") as fd:
            result = subprocess.run(command(port,no_launch),stdin=fd,capture_output=True,timeout=8)
        assert json.loads(result.stderr) == {"error":"private_pipe_required"}
        no_launch.chmod(0o722)
        broker(port,no_launch,expected="trusted_executable_required")
        no_launch.chmod(0o700)
        symlink = root/"not-direct"
        symlink.symlink_to(no_launch)
        broker(port,symlink,expected="trusted_executable_required")

        foreign = fixture(root,"wrong-scope",'''
start=json.loads(sys.stdin.readline())
m={"platform":"qq","account_id":"foreign","conversation_id":"room","message_id":"id",
   "timestamp":1,"is_self":False,"text":"test"}
print(json.dumps(dict(protocol=1,type="batch",sequence=1,
  batch=dict(idempotency_key="fixture",messages=[m]))),flush=True)
print("hidden child input",file=sys.stderr,flush=True)
''')
        broker(port,foreign,expected="plugin_scope_mismatch")
        assert runtime.get(port,STATUS,a) == checkpoint
        assert runtime.get(port,STATUS,b)["sequence"] == 0
        assert runtime.get(port,"/api/v1/messages?conversation_id=room",d)["items"] == []
        duplicate = fixture(root,"duplicate-json","sys.stdin.readline()\nprint('{\"protocol\":1,\"protocol\":1,\"type\":\"idle\"}',flush=True)\n")
        broker(port,duplicate,expected="invalid_json")
        too_large = fixture(root,"oversized","sys.stdin.readline()\nprint('x'*(531*1024),flush=True)\n")
        broker(port,too_large,expected="frame_too_large")
        root_pid = root/"source-pid"
        stall = fixture(root,"stalled",f"""sys.stdin.readline()
with open({str(root_pid)!r},"w") as f:
    f.write(str(os.getpid()))
time.sleep(30)
""")
        broker(port,stall,expected="pipe_timeout")
        assert not Path(f"/proc/{root_pid.read_text()}").exists()
        root_pid.unlink()

        # Graceful SIGTERM of the broker itself cleans the trusted foreground
        # plugin and emits a redacted error without submitting data.
        worker = subprocess.Popen(command(port,stall),stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            worker.stdin.write((json.dumps(payload)+"\n").encode())
            worker.stdin.flush()
            until = time.monotonic()+4
            while not root_pid.exists() or not root_pid.read_text():
                assert time.monotonic()<until
                time.sleep(0.01)
            child = root_pid.read_text()
            argv = Path(f"/proc/{child}/cmdline").read_bytes()
            env = Path(f"/proc/{child}/environ").read_bytes()
            clean_output([argv,env])
            assert b"synthetic" not in env and b"127.0.0.1" not in (argv+env)
            worker.send_signal(signal.SIGTERM)
            result_out,result_err = worker.communicate(timeout=3)
            assert worker.returncode == 1 and not result_out
            assert json.loads(result_err) == {"error":"broker_interrupted"}
            assert not Path(f"/proc/{child}").exists()
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait(timeout=5)
        assert runtime.get(port,STATUS,a) == checkpoint

        assert broker(port) == {"status":"committed","sequence":1}
        assert runtime.get(port,STATUS,a)["sequence"] == 1
        assert runtime.get(port,STATUS,b)["sequence"] == 0
        rows = runtime.get(port,"/api/v1/messages?conversation_id=synthetic-room",d)["items"]
        assert len(rows) == 1 and (rows[0]["id"],rows[0]["text"]) == ("synthetic-message","合成消息")
        runtime.finish(proc,port)
        proc2, port2 = start()
        assert broker(port2) == {"status":"idle","sequence":1}
        cursor = runtime.get(port2,"/api/v1/events/checkpoint",d)
        assert cursor["checkpoint"] == 1

        # The Hub and plugin acknowledgment are not atomic. If the trusted
        # child fails after receiving success, a fresh run reconciles from
        # durable Hub sequence without submitting duplicate records.
        lost_ack = fixture(root,"lost-child-ack",'''
initial=json.loads(sys.stdin.readline())
m=dict(initial["scope"],conversation_id="ack-room",message_id="uncertain",
       timestamp=1,is_self=False,text="test")
print(json.dumps(dict(protocol=1,type="batch",sequence=1,
  batch=dict(idempotency_key="ack-batch",messages=[m]))),flush=True)
sys.stdin.readline()
sys.exit(1)
''')
        b_control = dict(token=b,grant_id="sdk.other",platform="qq",account_id="foreign")
        broker(port2,lost_ack,b_control,expected="plugin_exit_failed")
        assert runtime.get(port2,STATUS,b)["sequence"] == 1
        assert broker(port2,SYNTHETIC,b_control) == {"status":"idle","sequence":1}
        assert runtime.get(port2,"/api/v1/events/checkpoint",d) == cursor
        runtime.finish(proc2,port2)
    finally:
        for proc in tasks:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            for pipe in (proc.stdin,proc.stdout,proc.stderr):
                pipe.close()
    print("extracted source SDK: bounded trusted child, private pipe, strict local URI and manifest, child denial/redaction, timeout/interruption/reaping, durable one-record resume")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="lmh-sdk-test-") as tmp:
        check(Path(tmp))
