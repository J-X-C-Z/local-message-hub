#[cfg(target_os = "linux")]
use rustix::{
    process::{Pid, Signal, getppid, kill_process, set_parent_process_death_signal},
};
#[cfg(unix)]
use std::{fs, os::unix::fs::MetadataExt};
#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;
#[cfg(target_os = "macos")]
use crate::desktop_macos::Owner;
#[cfg(target_os = "windows")]
use crate::desktop_windows::Owner;
use serde::{Deserialize, Serialize};
use std::{
    io::{self, BufRead, BufReader, Read, Write}, net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream},
    path::Path, process::{Command, Child, Stdio},
    sync::mpsc::{self, Receiver, TryRecvError}, thread, time::{Duration, Instant},
};

const START: Duration = Duration::from_secs(5);
const STOP: Duration = Duration::from_millis(900);
const POLL: Duration = Duration::from_millis(20);
const HEALTH_IO: Duration = Duration::from_millis(250);

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Ready {
    event: String,
    origin: String,
    api_version: u8,
    mode: String,
}

fn issue(message: &'static str) -> io::Error { io::Error::other(message) }

#[cfg(unix)]
pub(crate) fn private_grants(path: &Path) -> io::Result<()> {
    let meta = fs::symlink_metadata(path).map_err(|_| issue("private grant file unavailable"))?;
    if !meta.is_file() || meta.mode() & 0o077 != 0 || meta.nlink() != 1 {
        return Err(issue("expected a private regular grant file"));
    }
    Ok(())
}

#[cfg(target_os = "windows")]
pub(crate) fn private_grants(path: &Path) -> io::Result<()> {
    // Local regular-file validation only; the desktop owner supplies private NTFS ACLs.
    crate::desktop_windows::local_file(path)
}

#[cfg(target_os = "linux")]
pub(crate) fn arm_parent_death(parent: i32) -> io::Result<()> {
    let expected = Pid::from_raw(parent).filter(|pid| pid.as_raw_pid() > 1)
        .ok_or_else(|| issue("invalid supervisor parent"))?;
    // Arm first, then check parent to cover a supervisor exit during exec/boot.
    set_parent_process_death_signal(Some(Signal::KILL))?;
    if getppid() != Some(expected) { return Err(issue("supervisor parent lost")); }
    Ok(())
}

struct Managed {
    child: Child,
    #[cfg(any(target_os = "macos", target_os = "windows"))]
    ordinary_live: bool,
    #[cfg(any(target_os = "macos", target_os = "windows"))]
    owner: Option<Owner>,
}
impl Managed {
    fn stopped_without_signal(&self,status:std::process::ExitStatus)->bool {
        #[cfg(any(target_os = "macos", target_os = "windows"))]
        if self.ordinary_live { return !status.success(); }
        let _=status;
        false
    }
    fn stop(&mut self) -> io::Result<bool> {
        #[cfg(any(target_os = "macos", target_os = "windows"))]
        drop(self.owner.take());
        if let Some(status)=self.child.try_wait()? { return Ok(self.stopped_without_signal(status)); }
        #[cfg(target_os = "linux")]
        if let Some(pid) = i32::try_from(self.child.id()).ok().and_then(Pid::from_raw) {
            let _ = kill_process(pid, Signal::INT);
        }
        let grace=STOP;
        #[cfg(target_os = "macos")]
        let grace=if self.ordinary_live { crate::desktop_macos::LIVE_DRAIN_LIMIT+STOP } else { grace };
        #[cfg(target_os = "windows")]
        let grace=if self.ordinary_live { crate::desktop_windows::LIVE_DRAIN_LIMIT+STOP } else { grace };
        let deadline = Instant::now() + grace;
        loop {
            if let Some(status)=self.child.try_wait()? { return Ok(self.stopped_without_signal(status)); }
            if Instant::now() >= deadline { break; }
            thread::sleep(POLL);
        }
        let _ = self.child.kill();
        // Poll reap after SIGKILL, rather than hang on an uninterruptible task.
        let deadline = Instant::now() + STOP;
        loop {
            if self.child.try_wait()?.is_some() { return Ok(true); }
            if Instant::now() >= deadline { return Err(issue("backend shutdown timed out")); }
            thread::sleep(POLL);
        }
    }
}
impl Drop for Managed {
    fn drop(&mut self) {
        if matches!(self.child.try_wait(),Ok(None)) { let _ = self.stop(); }
    }
}

fn line_read(reader: impl BufRead, max: u64) -> io::Result<Vec<u8>> {
    let mut line = Vec::new();
    reader.take(max + 1).read_until(b'\n', &mut line)?;
    if line.len() as u64 > max { return Err(issue("control frame too long")); }
    Ok(line)
}

fn watch_stdin() -> Receiver<io::Result<()>> {
    let (send, recv) = mpsc::sync_channel(1);
    thread::spawn(move || {
        let command = line_read(io::stdin().lock(),5).and_then(|line| {
            if line.is_empty() || line == b"stop\n" { Ok(()) }
            else { Err(issue("invalid supervisor command")) }
        });
        let _ = send.send(command);
    });
    recv
}

fn stop_requested(control: &Receiver<io::Result<()>>) -> io::Result<bool> {
    match control.try_recv() {
        Ok(value) => value.map(|()| true),
        Err(TryRecvError::Empty) => Ok(false),
        Err(TryRecvError::Disconnected) => Err(issue("supervisor control unavailable")),
    }
}

fn readiness(raw: &[u8], mode: &str) -> io::Result<(Ready, SocketAddr)> {
    if !raw.ends_with(b"\n") { return Err(issue("backend readiness missing")); }
    let frame: Ready = serde_json::from_slice(raw).map_err(|_| issue("invalid backend readiness"))?;
    let address: SocketAddr = frame.origin.strip_prefix("http://")
        .and_then(|s| s.parse().ok()).ok_or_else(|| issue("invalid backend readiness address"))?;
    if frame.event != "ready" || frame.api_version != 1 || frame.mode != mode
        || address.ip() != IpAddr::V4(Ipv4Addr::LOCALHOST) || address.port() == 0
        || frame.origin != format!("http://{address}")
    { return Err(issue("invalid backend readiness")); }
    Ok((frame,address))
}

fn probe(address: SocketAddr) -> io::Result<()> {
    let mut stream = TcpStream::connect_timeout(&address,HEALTH_IO)?;
    stream.set_write_timeout(Some(HEALTH_IO))?;
    stream.set_read_timeout(Some(HEALTH_IO))?;
    write!(stream,"GET /healthz HTTP/1.1\r\nHost: {address}\r\nConnection: close\r\n\r\n")?;
    // Read under an overall deadline as well as socket inactivity timeouts.
    let deadline = Instant::now() + HEALTH_IO;
    let mut reply = Vec::new();
    let mut chunk = [0;512];
    loop {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() { return Err(issue("backend health probe timed out")); }
        stream.set_read_timeout(Some(remaining))?;
        let count = stream.read(&mut chunk)?;
        if count == 0 { break; }
        if reply.len() + count > 4096 { return Err(issue("invalid backend health reply")); }
        reply.extend_from_slice(&chunk[..count]);
    }
    let divider = reply.windows(4).position(|s| s == b"\r\n\r\n")
        .ok_or_else(|| issue("invalid backend health reply"))?;
    if !reply.starts_with(b"HTTP/1.1 200 ") || reply[divider+4..] != *b"{\"status\":\"ok\"}" {
        return Err(issue("backend health probe failed"));
    }
    Ok(())
}

pub(crate) fn run(flag: &str, path: &Path, grants: &Path, mode: &str) -> io::Result<()> {
    private_grants(grants)?;
    let executable = std::env::current_exe().map_err(|_| issue("runtime binary unavailable"))?;
    #[cfg(target_os = "windows")]
    {
        match (flag, mode) {
            ("--snapshot", "snapshot-read-only") => crate::desktop_windows::local_file(path)?,
            ("--live", "native-live") => crate::desktop_windows::local_directory(path)?,
            _ => return Err(issue("unsupported Windows managed archive mode")),
        }
        crate::desktop_windows::local_file(&executable)?;
    }
    let mut command = Command::new(executable);
    command.arg("desktop-child");
    #[cfg(target_os = "linux")]
    command.arg(std::process::id().to_string()).stdin(Stdio::null());
    #[cfg(any(target_os = "macos", target_os = "windows"))]
    command.stdin(Stdio::piped());
    #[cfg(target_os = "windows")]
    command.creation_flags(crate::desktop_windows::CREATE_NO_WINDOW);
    let mut child = Managed {
        child: command.arg(flag).arg(path).arg("--grants").arg(grants)
            .env_clear().stdout(Stdio::piped()).stderr(Stdio::null())
            .spawn().map_err(|_| issue("native backend could not start"))?,
        #[cfg(any(target_os = "macos", target_os = "windows"))]
        owner: None,
        #[cfg(any(target_os = "macos", target_os = "windows"))]
        ordinary_live: mode=="native-live",
    };
    #[cfg(any(target_os = "macos", target_os = "windows"))]
    {
        child.owner = Some(Owner::arm(&mut child.child)?);
    }
    let ready_stdout = child.child.stdout.take().ok_or_else(|| issue("backend readiness unavailable"))?;
    let (send, receive) = mpsc::sync_channel(1);
    thread::spawn(move || {
        let _ = send.send(line_read(BufReader::new(ready_stdout),1024));
    });
    let stop = watch_stdin();
    let deadline = Instant::now() + START;
    let mut stdout = io::stdout().lock();
    loop {
        if stop_requested(&stop)? {
            return report_stop(&mut child,&mut stdout);
        }
        if child.child.try_wait()?.is_some() { return Err(issue("native backend exited before readiness")); }
        if Instant::now() >= deadline { return Err(issue("native backend startup timed out")); }
        match receive.recv_timeout(POLL) {
            Ok(bytes) => {
                let (frame,address) = readiness(&bytes?,mode)?;
                probe(address).map_err(|_| issue("native backend health check failed"))?;
                if stop_requested(&stop)? { return report_stop(&mut child,&mut stdout); }
                if child.child.try_wait()?.is_some() { return Err(issue("native backend exited before readiness")); }
                writeln!(stdout,"{}",serde_json::json!({
                    "event":frame.event, "origin":frame.origin, "api_version":frame.api_version,
                    "mode":frame.mode,"backend_pid":child.child.id(),
                }))?;
                stdout.flush()?;
                break;
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => return Err(issue("native backend readiness unavailable")),
        }
    }
    loop {
        if stop_requested(&stop)? { return report_stop(&mut child,&mut stdout); }
        if child.child.try_wait()?.is_some() { return Err(issue("native backend exited unexpectedly")); }
        thread::sleep(POLL);
    }
}

fn report_stop(child: &mut Managed, mut stdout: impl Write) -> io::Result<()> {
    let forced = child.stop()?;
    writeln!(stdout,"{{\"event\":\"stopped\",\"forced\":{forced}}}")?;
    stdout.flush()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn readiness_requires_exact_local_origin_version_mode_and_fields() {
        let good = r#"{"event":"ready","origin":"http://127.0.0.1:1234","api_version":1,"mode":"native-live"}"#;
        let parse = |line: &str| readiness(format!("{line}\n").as_bytes(),"native-live");
        assert!(parse(good).is_ok());
        for invalid in [
            good.replace("127.0.0.1","127.0.0.2"),
            good.replace("127.0.0.1","localhost"),
            good.replace("1234","0"),
            good.replace(":1234",":1234/path"),
            good.replace("native-live","snapshot-read-only"),
            good.replace("\"api_version\":1","\"api_version\":2"),
            good.replace("\"event\":\"ready\"","\"event\":\"ready\",\"unknown\":\"untrusted\""),
        ] {
            assert!(parse(&invalid).is_err());
        }
        assert!(readiness(good.as_bytes(),"native-live").is_err());
    }

    #[test]
    fn line_reader_enforces_control_bound() {
        assert_eq!(line_read(Cursor::new(b"stop\n"),5).unwrap(),b"stop\n");
        assert!(line_read(Cursor::new(b"oversized frame\n"),5).is_err());
        assert!(line_read(Cursor::new(b""),5).unwrap().is_empty());
    }
}
